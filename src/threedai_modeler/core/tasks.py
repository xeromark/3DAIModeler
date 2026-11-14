"""Tareas en hilos de fondo para que Blender nunca se congele (RNF-02).

Los hilos nunca tocan `bpy`. Reportan progreso aquí y la interfaz lo lee; el callback
`on_done` se ejecuta en el hilo principal cuando la interfaz llama a `take_finished()`.
"""

from __future__ import annotations

import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from enum import StrEnum
from typing import Any


class TaskBusyError(RuntimeError):
    """Ya hay una tarea de ese tipo en curso."""


class TaskCancelledError(RuntimeError):
    """La tarea vio la señal de cancelación."""


class TaskStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskContext:
    """Lo que recibe la función de la tarea para reportar y ver si la cancelaron."""

    def __init__(self, task: Task) -> None:
        self._task = task
        self.cancel = task.cancel_event

    def report(self, message: str, fraction: float | None = None) -> None:
        self._task.message = message
        self._task.fraction = fraction

    def log(self, line: str) -> None:
        self._task.logs.append(line)

    def check_cancelled(self) -> None:
        if self.cancel.is_set():
            raise TaskCancelledError


TaskFn = Callable[[TaskContext], Any]
DoneFn = Callable[["Task"], None]


class Task:
    def __init__(self, kind: str, title: str, fn: TaskFn, on_done: DoneFn | None = None) -> None:
        self.kind = kind
        self.title = title
        self._fn = fn
        self.on_done = on_done
        self.status = TaskStatus.RUNNING
        self.message = ""
        self.fraction: float | None = None
        self.error: str | None = None
        self.result: Any = None
        self.logs: deque[str] = deque(maxlen=500)
        self.cancel_event = threading.Event()
        self.started = time.monotonic()
        self.finished: float | None = None
        self.handled = False
        self._thread = threading.Thread(target=self._run, name=f"threedai-{kind}", daemon=True)

    @property
    def running(self) -> bool:
        return self.status is TaskStatus.RUNNING

    @property
    def elapsed(self) -> float:
        return (self.finished or time.monotonic()) - self.started

    def start(self) -> Task:
        self._thread.start()
        return self

    def cancel(self) -> None:
        self.cancel_event.set()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def _run(self) -> None:
        try:
            self.result = self._fn(TaskContext(self))
        except BaseException as exc:
            self.status = TaskStatus.CANCELLED if self.cancel_event.is_set() else TaskStatus.FAILED
            self.error = str(exc) or type(exc).__name__
            self.logs.append("".join(traceback.format_exception(exc)))
        else:
            self.status = TaskStatus.DONE
        finally:
            self.finished = time.monotonic()


class TaskRegistry:
    """Una tarea activa por tipo ('engine', 'download', 'generate')."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}

    def start(self, kind: str, title: str, fn: TaskFn, on_done: DoneFn | None = None) -> Task:
        with self._lock:
            current = self._tasks.get(kind)
            if current is not None and current.running:
                raise TaskBusyError(f"ya hay una tarea en curso: {current.title}")
            task = Task(kind, title, fn, on_done)
            self._tasks[kind] = task
        return task.start()

    def get(self, kind: str) -> Task | None:
        with self._lock:
            return self._tasks.get(kind)

    def any_running(self) -> bool:
        with self._lock:
            return any(t.running for t in self._tasks.values())

    def take_finished(self) -> list[Task]:
        """Tareas terminadas cuyo `on_done` aún no se ejecutó (llamar desde el hilo principal)."""
        with self._lock:
            finished = [t for t in self._tasks.values() if not t.running and not t.handled]
            for task in finished:
                task.handled = True
        return finished

    def cancel_all(self) -> None:
        with self._lock:
            tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
