"""Agregar pesos que el usuario ya tiene en su PC (p. ej. bajados a mano de Hugging Face).

1. Se lee solo la cabecera del .safetensors para reconocer la arquitectura y la precisión.
2. Se calcula el SHA-256: si coincide con un archivo del catálogo, se instala como ese archivo
   oficial (sin volver a descargarlo).
3. Si no, se registra como variante de la familia reconocida.
El archivo siempre termina dentro de engine/ (se mueve o se copia).
"""

from __future__ import annotations

import hashlib
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .paths import Workspace
from .registry import CHUNK, LOCAL_REPO, FileSpec, Registry, RegistryError, _slug
from .safetensors_info import FileSignature, detect_family, load_family_fingerprints, signature

ProgressFn = Callable[[str, float | None], None]


class ImportMode(StrEnum):
    MOVE = "move"
    COPY = "copy"


@dataclass(frozen=True)
class ImportResult:
    official: bool
    """True si el archivo es idéntico a uno del catálogo."""
    file_id: str
    model_ids: tuple[str, ...]
    family: str
    precision: str
    path: Path


class ImportCancelledError(RuntimeError):
    """El usuario canceló la importación."""


def analyze(ws: Workspace, src: Path) -> tuple[FileSignature, str | None]:
    """Reconoce arquitectura y precisión leyendo solo la cabecera (rápido)."""
    if src.suffix != ".safetensors":
        raise RegistryError("solo se aceptan archivos .safetensors")
    sig = signature(src)
    return sig, detect_family(sig, load_family_fingerprints(ws.signatures_file))


def sha256_file(path: Path, progress: ProgressFn | None, cancel: threading.Event | None) -> str:
    digest = hashlib.sha256()
    total = path.stat().st_size or 1
    done = 0
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            if cancel is not None and cancel.is_set():
                raise ImportCancelledError
            digest.update(chunk)
            done += len(chunk)
            if progress:
                progress("Verificando (SHA-256)", done / total)
    return digest.hexdigest()


def _place(src: Path, dest: Path, mode: ImportMode, progress: ProgressFn | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    if mode is ImportMode.MOVE:
        try:
            src.replace(dest)  # mismo disco: instantáneo
            return
        except OSError:
            pass  # otro disco: copiar y borrar el original
    total = src.stat().st_size or 1
    done = 0
    with src.open("rb") as fin, tmp.open("wb") as fout:
        while chunk := fin.read(CHUNK):
            fout.write(chunk)
            done += len(chunk)
            if progress:
                progress("Copiando al proyecto", done / total)
    shutil.copystat(src, tmp)
    tmp.replace(dest)
    if mode is ImportMode.MOVE:
        src.unlink()


def import_local(
    ws: Workspace,
    registry: Registry,
    src: Path,
    *,
    mode: ImportMode = ImportMode.MOVE,
    name: str = "",
    license: str = "desconocida",
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> ImportResult:
    src = src.expanduser().resolve()
    if not src.is_file():
        raise RegistryError(f"no existe el archivo {src}")
    if src.is_relative_to(ws.comfy_models_dir.resolve()):
        raise RegistryError("el archivo ya está dentro de la carpeta de modelos del proyecto")
    if progress:
        progress("Leyendo la cabecera", None)
    sig, family = analyze(ws, src)
    sha = sha256_file(src, progress, cancel)

    official = registry.find_by_hash(sha, sig.size)
    if official is not None:
        dest = registry.local_path(ws, official)
        if registry.is_file_installed(ws, official):
            raise RegistryError(f"ese archivo ya está instalado ({official.filename})")
        _place(src, dest, mode, progress)
        users = registry.models_using(official.id)
        return ImportResult(
            True,
            official.id,
            tuple(m.id for m in users),
            users[0].family if users else "",
            sig.precision,
            dest,
        )

    if family is None:
        raise RegistryError(
            f"arquitectura no reconocida ({sig.tensors} tensores): no es compatible con "
            "ninguna familia soportada"
        )
    file_id = _slug(f"user-local-{src.stem}-{sha[:8]}")
    dest = ws.comfy_models_dir / "diffusion_models" / f"{src.stem}-{sha[:8]}{src.suffix}"
    weights = FileSpec(
        file_id, LOCAL_REPO, LOCAL_REPO, dest.name, "diffusion_models", sig.size, sha
    )
    model = registry.make_variant(
        weights, family=family, name=name or src.stem, precision=sig.precision, license=license
    )
    _place(src, dest, mode, progress)
    registry.add_user_variant(ws, weights, model)
    return ImportResult(False, file_id, (model.id,), family, sig.precision, dest)
