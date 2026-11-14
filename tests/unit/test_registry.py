import hashlib
import io
import json
import threading
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from threedai_modeler.core.paths import Workspace
from threedai_modeler.core.registry import (
    DownloadCancelledError,
    DownloadError,
    FileSpec,
    ModelState,
    Registry,
    RegistryError,
    download_file,
    fetch_hf_file,
    unapproved_orgs,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = bytes(range(256)) * 4096  # 1 MiB
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


def file_block(fid: str, path: str, *, size: int = 10, sha: str = "a" * 64) -> str:
    return f"""
[files.{fid}]
repo = "Comfy-Org/X"
revision = "abc"
path = "{path}"
dest = "diffusion_models"
size = {size}
sha256 = "{sha}"
"""


def model_block(mid: str, files: list[str], default: bool = False) -> str:
    return f"""
[[models]]
id = "{mid}"
name = "{mid}"
family = "fam"
roles = ["shape"]
files = {files!r}
default = {str(default).lower()}
""".replace("'", '"')


def write_registry(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "registry.toml"
    path.write_text("schema = 1\n" + body)
    return path


def test_official_registry_is_valid() -> None:
    reg = Registry.load(REPO_ROOT / "models" / "registry.toml")
    default = reg.default_model()
    assert default is not None and default.id == "pixal3d-int8"
    for spec in reg.files.values():
        assert spec.is_safe_format
    assert unapproved_orgs(reg.files.values()) == set()


def test_unknown_file_reference(tmp_path: Path) -> None:
    path = write_registry(tmp_path, file_block("a", "a.safetensors") + model_block("m", ["b"]))
    with pytest.raises(RegistryError, match="desconocidos"):
        Registry.load(path)


def test_rejects_pickle_formats(tmp_path: Path) -> None:
    path = write_registry(tmp_path, file_block("a", "model.ckpt"))
    with pytest.raises(RegistryError, match=r"no es \.safetensors"):
        Registry.load(path)


def test_rejects_dest_traversal(tmp_path: Path) -> None:
    body = file_block("a", "a.safetensors").replace('"diffusion_models"', '"../../etc"')
    with pytest.raises(RegistryError, match="relativa"):
        Registry.load(write_registry(tmp_path, body))


def test_user_registry_cannot_redefine(tmp_path: Path) -> None:
    official = write_registry(tmp_path, file_block("a", "a.safetensors"))
    user = tmp_path / "user.toml"
    user.write_text("schema = 1\n" + file_block("a", "b.safetensors"))
    with pytest.raises(RegistryError, match="duplicado"):
        Registry.load(official, user)


def test_state_and_shared_file_removal(tmp_path: Path) -> None:
    body = (
        file_block("shared", "shared.safetensors")
        + file_block("one", "one.safetensors")
        + file_block("two", "two.safetensors")
        + model_block("m1", ["shared", "one"])
        + model_block("m2", ["shared", "two"])
    )
    reg = Registry.load(write_registry(tmp_path, body))
    ws = Workspace(tmp_path)

    assert reg.state(ws, "m1") is ModelState.MISSING
    for fid in ("shared", "one", "two"):
        path = reg.local_path(ws, reg.files[fid])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
    assert reg.state(ws, "m1") is ModelState.INSTALLED

    removed = reg.remove_model(ws, "m1")
    assert [p.name for p in removed] == ["one.safetensors"]  # "shared" lo usa m2
    assert reg.state(ws, "m1") is ModelState.PARTIAL
    assert reg.state(ws, "m2") is ModelState.INSTALLED


# ------------------------------------------------------------- descargas


class RangeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        start = 0
        if rng := self.headers.get("Range"):
            start = int(rng.removeprefix("bytes=").rstrip("-"))
            self.send_response(206)
        else:
            self.send_response(200)
        body = PAYLOAD[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/file"
    server.shutdown()


def spec(sha: str = PAYLOAD_SHA) -> FileSpec:
    return FileSpec("f", "Comfy-Org/X", "abc", "f.safetensors", "vae", len(PAYLOAD), sha)


def test_download_verifies_and_renames(tmp_path: Path, server_url: str) -> None:
    dest = tmp_path / "vae" / "f.safetensors"
    seen: list[int] = []
    download_file(spec(), dest, lambda n, _t: seen.append(n), url=server_url)
    assert dest.read_bytes() == PAYLOAD
    assert seen[-1] == len(PAYLOAD)
    assert not dest.with_name("f.safetensors.part").exists()


def test_download_resumes_part(tmp_path: Path, server_url: str) -> None:
    dest = tmp_path / "f.safetensors"
    dest.with_name("f.safetensors.part").write_bytes(PAYLOAD[:1000])
    download_file(spec(), dest, url=server_url)
    assert dest.read_bytes() == PAYLOAD


def test_download_bad_hash_is_discarded(tmp_path: Path, server_url: str) -> None:
    dest = tmp_path / "f.safetensors"
    with pytest.raises(DownloadError, match="SHA-256"):
        download_file(spec(sha="0" * 64), dest, url=server_url)
    assert not dest.exists()
    assert not dest.with_name("f.safetensors.part").exists()


def test_download_cancel_keeps_part(tmp_path: Path, server_url: str) -> None:
    dest = tmp_path / "f.safetensors"
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(DownloadCancelledError):
        download_file(spec(), dest, cancel=cancel, url=server_url)
    assert not dest.exists()


# ------------------------------------------------------------- variantes


def official_copy(tmp_path: Path) -> tuple[Workspace, Registry]:
    ws = Workspace(tmp_path)
    return ws, Registry.load(REPO_ROOT / "models" / "registry.toml", ws.user_registry_file)


def bf16_weights() -> FileSpec:
    return FileSpec(
        "user-comfy-org-trellis-2-trellis-2-bf16",
        "Comfy-Org/TRELLIS.2",
        "430a9d09",
        "diffusion_models/trellis_2_bf16.safetensors",
        "diffusion_models",
        10_340_000_000,
        "b" * 64,
    )


def test_variant_reuses_family_shared_files(tmp_path: Path) -> None:
    _ws, reg = official_copy(tmp_path)
    model = reg.make_variant(bf16_weights(), family="trellis2", name="bf16", precision="bf16")
    assert model.id == "trellis2-bf16"
    assert model.files[0] == bf16_weights().id
    assert "trellis2-int8" not in model.files  # pesos del modelo base excluidos
    assert set(model.files[1:]) == set(reg.model("trellis2-int8").files) - {"trellis2-int8"}


def test_variant_roundtrip_and_removal(tmp_path: Path) -> None:
    ws, reg = official_copy(tmp_path)
    weights = bf16_weights()
    model = reg.make_variant(
        weights, family="trellis2", name="bf16", precision="bf16", license="MIT"
    )
    reg.add_user_variant(ws, weights, model)

    official = REPO_ROOT / "models" / "registry.toml"
    again = Registry.load(official, ws.user_registry_file)
    loaded = again.model("trellis2-bf16")
    assert loaded.user_added and loaded.license == "MIT"
    assert again.files[weights.id] == weights

    again.remove_user_variant(ws, "trellis2-bf16")
    final = Registry.load(official, ws.user_registry_file)
    assert "trellis2-bf16" not in final.models
    assert weights.id not in final.files


def test_cannot_remove_official_as_variant(tmp_path: Path) -> None:
    ws, reg = official_copy(tmp_path)
    with pytest.raises(RegistryError, match="oficial"):
        reg.remove_user_variant(ws, "pixal3d-int8")


def test_variant_unknown_family(tmp_path: Path) -> None:
    _ws, reg = official_copy(tmp_path)
    with pytest.raises(RegistryError, match="familia"):
        reg.make_variant(bf16_weights(), family="otra", name="x", precision="bf16")


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        pass


def test_fetch_hf_file_pins_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "sha": "deadbeef" * 5,
        "siblings": [
            {
                "rfilename": "diffusion_models/m.safetensors",
                "size": 123,
                "lfs": {"sha256": "c" * 64, "size": 123},
            }
        ],
    }
    seen: list[str] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> FakeResponse:
        seen.append(req.full_url)
        return FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    spec = fetch_hf_file("Org/Repo", "diffusion_models/m.safetensors")
    assert spec.revision == "deadbeef" * 5
    assert spec.size == 123 and spec.sha256 == "c" * 64
    assert spec.id == "user-org-repo-m"
    assert seen[0].endswith("/api/models/Org/Repo/revision/main?blobs=true")


@pytest.mark.parametrize(
    ("siblings", "match"),
    [
        ([], "no tiene el archivo"),
        ([{"rfilename": "m.ckpt", "size": 1, "lfs": {"sha256": "c" * 64}}], "safetensors"),
        ([{"rfilename": "m.ckpt", "size": 1}], "SHA-256"),
    ],
)
def test_fetch_hf_file_rejects(
    monkeypatch: pytest.MonkeyPatch, siblings: list[dict[str, object]], match: str
) -> None:
    body = json.dumps({"sha": "a" * 40, "siblings": siblings}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: FakeResponse(body))
    with pytest.raises(RegistryError, match=match):
        fetch_hf_file("Org/Repo", "m.ckpt")


@pytest.mark.parametrize("repo", ["../etc", "Org/..", "Org", "a/b/c", "-x/y", "Org/Repo?x=1"])
def test_fetch_hf_file_invalid_repo(monkeypatch: pytest.MonkeyPatch, repo: str) -> None:
    def no_network(*_a: object, **_k: object) -> None:
        raise AssertionError("no debe consultar la red")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    with pytest.raises(RegistryError, match="inválido"):
        fetch_hf_file(repo, "x.safetensors")


BAD_PATHS = ["../x.safetensors", "/etc/x.safetensors", "a/../../b.safetensors"]


@pytest.mark.parametrize("path", BAD_PATHS)
def test_fetch_hf_file_invalid_path(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("red"))
    with pytest.raises(RegistryError, match="inválida"):
        fetch_hf_file("Org/Repo", path)
