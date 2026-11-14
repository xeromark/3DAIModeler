import hashlib
import json
import struct
from pathlib import Path

import pytest

from threedai_modeler.core.local_models import ImportMode, analyze, import_local
from threedai_modeler.core.paths import Workspace
from threedai_modeler.core.registry import FileSpec, ModelSpec, Registry, RegistryError
from threedai_modeler.core.safetensors_info import (
    SafetensorsError,
    TensorInfo,
    fingerprint,
    precision,
    read_header,
    signature,
)

ARCH = {"blocks.0.w": [4, 4], "blocks.0.b": [4], "head.w": [2, 4]}


def write_safetensors(path: Path, tensors: dict[str, list[int]], dtype: str = "BF16") -> Path:
    itemsize = {"BF16": 2, "F32": 4, "I8": 1}
    header: dict[str, object] = {"__metadata__": {"format": "pt"}}
    offset = 0
    for name, shape in tensors.items():
        n = itemsize[dtype]
        for d in shape:
            n *= d
        header[name] = {"dtype": dtype, "shape": shape, "data_offsets": [offset, offset + n]}
        offset += n
    raw = json.dumps(header).encode()
    path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\0" * offset)
    return path


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    ws = Workspace(tmp_path / "ws")
    (ws.root / "models").mkdir(parents=True)
    arch = {k: TensorInfo("BF16", tuple(v)) for k, v in ARCH.items()}
    ws.signatures_file.write_text(
        f'schema = 1\n[families.fam]\nfingerprint = "{fingerprint(arch)}"\ntensors = 3\n'
    )
    return ws


@pytest.fixture
def registry() -> Registry:
    reg = Registry()
    shared = FileSpec("vae", "Comfy-Org/X", "abc", "vae/v.safetensors", "vae", 1, "a" * 64)
    base = FileSpec("w", "Comfy-Org/X", "abc", "d/w.safetensors", "diffusion_models", 1, "b" * 64)
    reg.files = {"vae": shared, "w": base}
    reg.models = {
        "fam-int8": ModelSpec("fam-int8", "Fam", "fam", ("shape",), "int8", "MIT", ("w", "vae"))
    }
    return reg


def test_header_and_precision(tmp_path: Path) -> None:
    f = write_safetensors(tmp_path / "a.safetensors", ARCH)
    header = read_header(f)
    assert header["head.w"] == TensorInfo("BF16", (2, 4))
    assert precision(header) == "bf16"
    assert signature(f).tensors == 3


def test_quantized_weights_share_fingerprint(tmp_path: Path) -> None:
    bf16 = write_safetensors(tmp_path / "a.safetensors", ARCH)
    int8 = write_safetensors(
        tmp_path / "b.safetensors", {**ARCH, "blocks.0.w.weight_scale": [1]}, dtype="I8"
    )
    assert signature(bf16).fingerprint == signature(int8).fingerprint
    assert signature(int8).precision == "int8"


def test_rejects_non_safetensors(tmp_path: Path) -> None:
    bad = tmp_path / "x.safetensors"
    bad.write_bytes(b"PK\x03\x04 esto es un zip")
    with pytest.raises(SafetensorsError):
        read_header(bad)


def test_analyze_detects_family(ws: Workspace, tmp_path: Path) -> None:
    f = write_safetensors(tmp_path / "mine.safetensors", ARCH)
    _sig, family = analyze(ws, f)
    assert family == "fam"
    other = write_safetensors(tmp_path / "other.safetensors", {"x": [3]})
    assert analyze(ws, other)[1] is None


def test_import_unknown_architecture_leaves_file(
    ws: Workspace, registry: Registry, tmp_path: Path
) -> None:
    f = write_safetensors(tmp_path / "other.safetensors", {"x": [3]})
    with pytest.raises(RegistryError, match="no reconocida"):
        import_local(ws, registry, f)
    assert f.exists()


@pytest.mark.parametrize("mode", [ImportMode.MOVE, ImportMode.COPY])
def test_import_variant(
    ws: Workspace, registry: Registry, tmp_path: Path, mode: ImportMode
) -> None:
    f = write_safetensors(tmp_path / "finetune.safetensors", ARCH)
    result = import_local(ws, registry, f, mode=mode, name="mi fine-tune")
    assert not result.official
    assert result.family == "fam" and result.precision == "bf16"
    assert result.path.is_file() and result.path.is_relative_to(ws.comfy_models_dir)
    assert f.exists() is (mode is ImportMode.COPY)
    model = registry.model(result.model_ids[0])
    assert model.user_added and model.files[0] == result.file_id
    assert "vae" in model.files and "w" not in model.files
    assert registry.files[result.file_id].is_local
    # Persistido y recargable junto al catálogo.
    assert ws.user_registry_file.is_file()


def test_import_recognizes_official_file(ws: Workspace, registry: Registry, tmp_path: Path) -> None:
    f = write_safetensors(tmp_path / "descargado_a_mano.safetensors", ARCH)
    data = f.read_bytes()
    official = FileSpec(
        "w",
        "Comfy-Org/X",
        "abc",
        "d/w.safetensors",
        "diffusion_models",
        len(data),
        hashlib.sha256(data).hexdigest(),
    )
    registry.files["w"] = official
    result = import_local(ws, registry, f, mode=ImportMode.COPY)
    assert result.official and result.model_ids == ("fam-int8",)
    assert registry.local_path(ws, official).read_bytes() == data
    with pytest.raises(RegistryError, match="ya está instalado"):
        import_local(ws, registry, f, mode=ImportMode.COPY)


def test_import_rejects_file_already_in_models_dir(ws: Workspace, registry: Registry) -> None:
    inside = ws.comfy_models_dir / "diffusion_models" / "x.safetensors"
    inside.parent.mkdir(parents=True)
    write_safetensors(inside, ARCH)
    with pytest.raises(RegistryError, match="ya está dentro"):
        import_local(ws, registry, inside)
