"""Ejecuta scripts de worker/ con un Blender en modo background (proceso aparte)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ENV_BLENDER = "THREEDAI_BLENDER"
STEAM_BLENDER = Path.home() / ".local/share/Steam/steamapps/common/Blender/blender"


class BlenderNotFoundError(RuntimeError):
    """No se encontró el ejecutable de Blender."""


class WorkerError(RuntimeError):
    """El script de Blender terminó con error."""


def find_blender(explicit: Path | None = None) -> Path:
    """Orden: argumento (p. ej. bpy.app.binary_path), THREEDAI_BLENDER, PATH, Steam."""
    candidates = [
        explicit,
        Path(os.environ[ENV_BLENDER]) if os.environ.get(ENV_BLENDER) else None,
        Path(found) if (found := shutil.which("blender")) else None,
        STEAM_BLENDER,
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise BlenderNotFoundError(f"no se encontró Blender; define {ENV_BLENDER}")


def run_worker(
    blender: Path, script: Path, args: list[str], timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    cmd = [str(blender), "--background", "--factory-startup", "--python", str(script), "--", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-20:])
        raise WorkerError(f"{script.name} falló (código {result.returncode}):\n{tail}")
    return result
