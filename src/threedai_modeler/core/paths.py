"""Rutas del workspace. Ninguna otra parte del código debe armar rutas absolutas."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_WORKSPACE = "THREEDAI_WORKSPACE"
"""Variable de entorno para forzar la raíz del workspace."""


class WorkspaceNotFoundError(RuntimeError):
    """No se encontró la raíz del proyecto."""


def _is_workspace(path: Path) -> bool:
    return (path / "compose.yaml").is_file() and (path / "src" / "threedai_modeler").is_dir()


def find_workspace_root(start: Path | None = None) -> Path:
    """Devuelve la raíz del workspace.

    Orden: variable THREEDAI_WORKSPACE, luego subir desde `start` (por defecto,
    la ubicación de este archivo) hasta encontrar compose.yaml y src/threedai_modeler.
    """
    override = os.environ.get(ENV_WORKSPACE)
    if override:
        root = Path(override).expanduser().resolve()
        if not _is_workspace(root):
            raise WorkspaceNotFoundError(f"{ENV_WORKSPACE}={override} no es un workspace válido")
        return root

    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if _is_workspace(candidate):
            return candidate
    raise WorkspaceNotFoundError(f"No se encontró el workspace subiendo desde {here}")


@dataclass(frozen=True)
class Workspace:
    """Todas las rutas del proyecto, derivadas de la raíz."""

    root: Path

    @classmethod
    def discover(cls, start: Path | None = None) -> Workspace:
        return cls(find_workspace_root(start))

    # Versionado en git
    @property
    def compose_file(self) -> Path:
        return self.root / "compose.yaml"

    @property
    def registry_file(self) -> Path:
        return self.root / "models" / "registry.toml"

    @property
    def presets_file(self) -> Path:
        return self.root / "models" / "presets.toml"

    @property
    def workflows_dir(self) -> Path:
        return self.root / "workflows"

    @property
    def worker_dir(self) -> Path:
        return self.root / "worker"

    # Runtime (engine/, ignorado por git)
    @property
    def engine_dir(self) -> Path:
        return self.root / "engine"

    @property
    def comfy_dir(self) -> Path:
        return self.engine_dir / "ComfyUI"

    @property
    def comfy_models_dir(self) -> Path:
        return self.comfy_dir / "models"

    @property
    def comfy_input_dir(self) -> Path:
        return self.comfy_dir / "input"

    @property
    def comfy_output_dir(self) -> Path:
        return self.comfy_dir / "output"

    @property
    def jobs_dir(self) -> Path:
        return self.engine_dir / "jobs"

    @property
    def logs_dir(self) -> Path:
        return self.engine_dir / "logs"

    @property
    def cache_dir(self) -> Path:
        return self.engine_dir / ".cache"

    @property
    def user_registry_file(self) -> Path:
        return self.engine_dir / "user-registry.toml"
