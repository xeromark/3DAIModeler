"""Lee la cabecera de un .safetensors (sin cargar los pesos) y reconoce su arquitectura.

La huella de una arquitectura es el SHA-256 de sus tensores ("nombre:forma"), sin los tensores
auxiliares de cuantización. Así INT8, bf16 o un fine-tune de la misma red dan la misma huella.
"""

from __future__ import annotations

import hashlib
import json
import struct
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_HEADER = 100 * 1024 * 1024
QUANT_SUFFIXES = (".weight_scale", ".comfy_quant", ".input_scale", ".scale_weight")


class SafetensorsError(ValueError):
    """El archivo no es un .safetensors válido."""


@dataclass(frozen=True)
class TensorInfo:
    dtype: str
    shape: tuple[int, ...]


@dataclass(frozen=True)
class FileSignature:
    tensors: int
    """Tensores de la arquitectura (sin los de cuantización)."""
    fingerprint: str
    precision: str
    size: int


def read_header(path: Path) -> dict[str, TensorInfo]:
    """Cabecera de un .safetensors: nombre -> tipo y forma. No lee los pesos."""
    with path.open("rb") as fh:
        raw_len = fh.read(8)
        if len(raw_len) != 8:
            raise SafetensorsError(f"{path.name}: archivo demasiado corto")
        (length,) = struct.unpack("<Q", raw_len)
        if not 2 <= length <= MAX_HEADER:
            raise SafetensorsError(f"{path.name}: no es un .safetensors (cabecera inválida)")
        try:
            header: dict[str, Any] = json.loads(fh.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise SafetensorsError(f"{path.name}: cabecera ilegible") from err
    header.pop("__metadata__", None)
    try:
        return {
            name: TensorInfo(str(info["dtype"]), tuple(int(d) for d in info["shape"]))
            for name, info in header.items()
        }
    except (KeyError, TypeError, ValueError) as err:
        raise SafetensorsError(f"{path.name}: cabecera con formato inesperado") from err


def architecture_tensors(header: dict[str, TensorInfo]) -> dict[str, TensorInfo]:
    return {k: v for k, v in header.items() if not k.endswith(QUANT_SUFFIXES)}


def fingerprint(header: dict[str, TensorInfo]) -> str:
    lines = sorted(
        f"{name}:{'x'.join(map(str, info.shape))}"
        for name, info in architecture_tensors(header).items()
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def precision(header: dict[str, TensorInfo]) -> str:
    dtypes = Counter(info.dtype for info in header.values())
    if dtypes.get("I8"):
        return "int8"
    if any(d.startswith("F8") for d in dtypes):
        return "fp8"
    dominant = dtypes.most_common(1)[0][0] if dtypes else ""
    return {"BF16": "bf16", "F16": "fp16", "F32": "fp32"}.get(dominant, dominant.lower())


def signature(path: Path) -> FileSignature:
    header = read_header(path)
    return FileSignature(
        tensors=len(architecture_tensors(header)),
        fingerprint=fingerprint(header),
        precision=precision(header),
        size=path.stat().st_size,
    )


def load_family_fingerprints(path: Path) -> dict[str, str]:
    """models/signatures.toml: familia -> huella."""
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return {family: str(v["fingerprint"]) for family, v in data.get("families", {}).items()}


def detect_family(sig: FileSignature, fingerprints: dict[str, str]) -> str | None:
    return next((fam for fam, fp in fingerprints.items() if fp == sig.fingerprint), None)
