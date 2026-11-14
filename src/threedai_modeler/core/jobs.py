"""Trabajos de generación: estado por etapas guardado en disco para poder reanudar."""

from __future__ import annotations

import json
import secrets
import shutil
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .paths import Workspace

STAGES: tuple[str, ...] = ("generate", "parts", "postprocess")
"""Etapas en orden. 'parts' se marca como hecha sin trabajo si el trabajo no pide partes."""


class JobError(RuntimeError):
    """Trabajo inexistente o inválido."""


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class StageRecord:
    status: StageStatus = StageStatus.PENDING
    started: float | None = None
    finished: float | None = None
    error: str | None = None
    outputs: dict[str, str] = field(default_factory=dict)
    """Rutas relativas a la carpeta del trabajo."""
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class Job:
    id: str
    created: float
    input_file: str
    model_id: str
    quality: str
    target_faces: int
    seed: int
    stages: dict[str, StageRecord]
    name: str = "Modelo"
    """Nombre del objeto en Blender (por defecto, el nombre de la imagen)."""
    parts: bool = False
    """Separar el modelo en partes (P3-SAM)."""
    root: Path = field(default=Path(), repr=False, compare=False)

    @property
    def dir(self) -> Path:
        return self.root / self.id

    @property
    def input_path(self) -> Path:
        return self.dir / self.input_file

    def output_path(self, stage: str, key: str) -> Path | None:
        rel = self.stages[stage].outputs.get(key)
        return self.dir / rel if rel else None

    @property
    def is_done(self) -> bool:
        return all(s.status is StageStatus.DONE for s in self.stages.values())

    # ------------------------------------------------------------ disco

    @classmethod
    def create(
        cls,
        ws: Workspace,
        image: Path,
        *,
        model_id: str,
        quality: str,
        target_faces: int,
        seed: int,
        name: str | None = None,
        parts: bool = False,
    ) -> Job:
        if not image.is_file():
            raise JobError(f"no existe la imagen {image}")
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        job = cls(
            id=job_id,
            created=time.time(),
            input_file="input" + image.suffix.lower(),
            model_id=model_id,
            quality=quality,
            target_faces=target_faces,
            seed=seed,
            stages={stage: StageRecord() for stage in STAGES},
            name=name or image.stem,
            parts=parts,
            root=ws.jobs_dir,
        )
        job.dir.mkdir(parents=True)
        shutil.copy2(image, job.input_path)
        job.save()
        return job

    @classmethod
    def load(cls, ws: Workspace, job_id: str) -> Job:
        path = ws.jobs_dir / job_id / "job.json"
        if not path.is_file():
            raise JobError(f"no existe el trabajo {job_id}")
        data = json.loads(path.read_text())
        stages = {
            name: StageRecord(**{**raw, "status": StageStatus(raw["status"])})
            for name, raw in data.pop("stages").items()
        }
        for name in STAGES:  # trabajos creados antes de agregar etapas nuevas
            stages.setdefault(name, StageRecord())
        return cls(**data, stages=stages, root=ws.jobs_dir)

    @classmethod
    def list_all(cls, ws: Workspace) -> list[Job]:
        if not ws.jobs_dir.is_dir():
            return []
        ids = sorted(p.parent.name for p in ws.jobs_dir.glob("*/job.json"))
        return [cls.load(ws, i) for i in ids]

    def save(self) -> None:
        data = asdict(self)
        data.pop("root")
        tmp = self.dir / "job.json.tmp"
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(self.dir / "job.json")
