import hashlib
import threading
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
