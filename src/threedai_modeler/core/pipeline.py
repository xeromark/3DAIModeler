"""Ejecuta un trabajo etapa por etapa, saltando las ya completadas (reanudación)."""

from __future__ import annotations

import json
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .blender import find_blender, run_worker
from .comfy import ComfyClient, Event
from .jobs import Job, StageRecord, StageStatus
from .monitor import ResourceMonitor
from .paths import Workspace
from .presets import Presets
from .registry import ModelState, Registry
from .workflows import GenerationParams, build_workflow, output_files, set_input, stage_label

ProgressFn = Callable[["Progress"], None]


class PipelineError(RuntimeError):
    """Un requisito del trabajo no se cumple (modelo no instalado, motor apagado...)."""


@dataclass
class Progress:
    stage: str
    """Etapa del trabajo (generate, postprocess...)."""
    label: str
    """Paso visible para el usuario (Forma, Textura...)."""
    fraction: float | None = None
    """Avance del paso actual (0..1) si el nodo lo reporta."""


@dataclass
class _Timer:
    """Acumula segundos por etiqueta visible a partir de los eventos 'executing'."""

    current: str | None = None
    since: float = 0.0
    totals: dict[str, float] = field(default_factory=dict)

    def switch(self, label: str | None) -> None:
        now = time.monotonic()
        if self.current is not None:
            self.totals[self.current] = self.totals.get(self.current, 0.0) + now - self.since
        self.current, self.since = label, now

    def rounded(self) -> dict[str, float]:
        return {k: round(v, 1) for k, v in self.totals.items()}


class Pipeline:
    def __init__(
        self,
        ws: Workspace,
        registry: Registry,
        presets: Presets,
        client: ComfyClient,
        blender: Path | None = None,
    ) -> None:
        self.ws = ws
        self.registry = registry
        self.presets = presets
        self.client = client
        self.blender = blender
        """Ejecutable de Blender para el postproceso (en el addon: bpy.app.binary_path)."""

    def check_ready(self, job: Job) -> None:
        if job.stages["generate"].status is not StageStatus.DONE:
            if self.registry.state(self.ws, job.model_id) is not ModelState.INSTALLED:
                raise PipelineError(f"el modelo {job.model_id} no está descargado")
            if not self.client.is_ready():
                raise PipelineError(f"el motor no responde en {self.client.base_url}")
        self.blender = find_blender(self.blender)

    def run(
        self,
        job: Job,
        on_progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> Job:
        self.check_ready(job)
        steps = {"generate": self._generate, "postprocess": self._postprocess}
        for name, record in job.stages.items():
            if record.status is StageStatus.DONE:
                continue
            record.status, record.started, record.error = StageStatus.RUNNING, time.time(), None
            job.save()
            try:
                steps[name](job, record, on_progress, cancel)
            except BaseException as exc:
                record.status = StageStatus.FAILED
                record.error = "".join(traceback.format_exception_only(exc)).strip()
                record.finished = time.time()
                job.save()
                raise
            record.status, record.finished = StageStatus.DONE, time.time()
            job.save()
        return job

    # ------------------------------------------------------------ etapas

    def _vram_used(self) -> float | None:
        device = self.client.system_stats()["devices"][0]
        return float(device["vram_total"] - device["vram_free"])

    def _generate(
        self,
        job: Job,
        record: StageRecord,
        on_progress: ProgressFn | None,
        cancel: threading.Event | None,
    ) -> None:
        def report(label: str, fraction: float | None = None) -> None:
            if on_progress:
                on_progress(Progress("generate", label, fraction))

        report("Subiendo imagen")
        image = self.client.upload_image(job.input_path, name=f"{job.id}{job.input_path.suffix}")
        params = GenerationParams(
            image=image,
            model_id=job.model_id,
            quality=self.presets.get_quality(job.quality),
            target_faces=job.target_faces,
            seed=job.seed,
        )
        workflow = build_workflow(self.ws.workflows_dir, self.registry, params)
        set_input(workflow, "save", "filename_prefix", f"threedai/{job.id}")

        timer = _Timer()
        current: dict[str, str] = {"label": ""}

        def on_event(event: Event) -> None:
            data: dict[str, Any] = event.data
            if event.type == "executing" and data.get("node"):
                label = stage_label(str(data["node"]))
                timer.switch(label)
                current["label"] = label
                report(label)
            elif event.type == "progress" and data.get("max"):
                report(current["label"], float(data["value"]) / float(data["max"]))

        try:
            with ResourceMonitor(self._vram_used) as monitor:
                outputs = self.client.run(workflow, on_event, cancel)
                timer.switch(None)
        finally:
            # Liberar VRAM/RAM pase lo que pase (RNF-01).
            report("Liberando memoria")
            self.client.free()

        files = output_files(outputs)
        if not files:
            raise PipelineError("el motor no devolvió ningún modelo 3D")
        report("Descargando resultado")
        dest = job.dir / "generate" / "model.glb"
        self.client.download(files[0], dest)
        record.outputs = {"glb": str(dest.relative_to(job.dir))}
        record.metrics = {**monitor.as_dict(), "stage_seconds": timer.rounded()}

    def _postprocess(
        self,
        job: Job,
        record: StageRecord,
        on_progress: ProgressFn | None,
        _cancel: threading.Event | None,
    ) -> None:
        if on_progress:
            on_progress(Progress("postprocess", "Creando .blend"))
        glb = job.output_path("generate", "glb")
        if glb is None or not glb.is_file():
            raise PipelineError("falta el GLB de la etapa generate")
        blend = job.dir / "postprocess" / "model.blend"
        assert self.blender is not None  # lo resuelve check_ready
        args = [str(glb), str(blend), "--name", job.name, "--target-faces", str(job.target_faces)]
        started = time.monotonic()
        result = run_worker(self.blender, self.ws.worker_dir / "postprocess.py", args, timeout=1800)
        line = next((ln for ln in result.stdout.splitlines() if ln.startswith("RESULT ")), None)
        if line is None:
            raise PipelineError("el worker de Blender no devolvió resultado")
        info: dict[str, Any] = json.loads(line.removeprefix("RESULT "))
        record.outputs = {"blend": str(blend.relative_to(job.dir))}
        record.metrics = {**info, "seconds": round(time.monotonic() - started, 1)}
