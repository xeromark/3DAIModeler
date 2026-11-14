"""Estado compartido del addon. Sin bpy: los hilos de fondo pueden usarlo con seguridad."""

from __future__ import annotations

import threading
from pathlib import Path

from ..core.comfy import ComfyClient
from ..core.engine import EngineManager, EngineState, default_manager, engine_disk_usage
from ..core.jobs import Job
from ..core.paths import Workspace
from ..core.presets import Presets
from ..core.registry import ModelState, Registry
from ..core.tasks import TaskRegistry

POLL_SECONDS = 3.0
DISK_EVERY = 20
"""Recalcular el uso de disco cada N sondeos (~1 min)."""


class Runtime:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws
        self.client = ComfyClient()
        self.manager: EngineManager = default_manager(ws, self.client)
        self.tasks = TaskRegistry()
        self.registry = Registry.for_workspace(ws)
        self.presets = Presets.load(ws.presets_file)

        self.engine_state = EngineState.STOPPED
        self.engine_message = "Consultando..."
        self.model_states: dict[str, ModelState] = {}
        self.recent_jobs: list[Job] = []
        self.last_error: str | None = None
        self.engine_disk_bytes: int | None = None
        self.models_disk_bytes = 0
        self._polls = 0
        self.version = 0
        """Sube cada vez que cambia algo visible: la interfaz se redibuja."""

        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll_loop, name="threedai-poll", daemon=True)

    # ------------------------------------------------------------- sondeo

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.tasks.cancel_all()

    def refresh(self) -> None:
        """Pide un sondeo inmediato (tras iniciar/detener, descargar...)."""
        self._wake.set()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self._poll_once()
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()

    def _poll_once(self) -> None:
        try:
            state, message = self.manager.status()
            states = {m: self.registry.state(self.ws, m) for m in self.registry.models}
            jobs = Job.list_all(self.ws)[-8:][::-1]
            installed = {
                f.id: f.size
                for m in self.registry.models
                for f in self.registry.files_of(m)
                if self.registry.is_file_installed(self.ws, f)
            }
            self.models_disk_bytes = sum(installed.values())
            if self._polls % DISK_EVERY == 0:
                self.engine_disk_bytes = engine_disk_usage(self.ws)
            self._polls += 1
        except Exception as exc:  # el sondeo nunca debe morir
            state, message, states, jobs = EngineState.UNAVAILABLE, str(exc), {}, []
        changed = (
            state != self.engine_state
            or message != self.engine_message
            or states != self.model_states
            or [j.id for j in jobs] != [j.id for j in self.recent_jobs]
            or [j.is_done for j in jobs] != [j.is_done for j in self.recent_jobs]
        )
        self.engine_state, self.engine_message = state, message
        self.model_states, self.recent_jobs = states, jobs
        if changed:
            self.version += 1

    def reload_registry(self) -> None:
        self.registry = Registry.for_workspace(self.ws)
        self._polls = 0  # recalcular disco
        self.refresh()


_runtime: Runtime | None = None


def init(workspace_override: str = "") -> Runtime:
    global _runtime
    start = Path(workspace_override).expanduser() if workspace_override else Path(__file__)
    _runtime = Runtime(Workspace.discover(start))
    _runtime.start()
    return _runtime


def get() -> Runtime:
    if _runtime is None:
        raise RuntimeError("3DAI Modeler no está inicializado")
    return _runtime


def shutdown() -> Runtime | None:
    global _runtime
    rt, _runtime = _runtime, None
    if rt is not None:
        rt.stop()
    return rt
