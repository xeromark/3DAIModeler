import threading

import pytest

from threedai_modeler.core.tasks import (
    TaskBusyError,
    TaskContext,
    TaskRegistry,
    TaskStatus,
)


def test_task_reports_and_finishes() -> None:
    reg = TaskRegistry()

    def work(ctx: TaskContext) -> int:
        ctx.report("paso", 0.5)
        ctx.log("hola")
        return 42

    task = reg.start("generate", "Generar", work)
    task.join(2)
    assert task.status is TaskStatus.DONE
    assert task.result == 42
    assert task.message == "paso" and task.fraction == 0.5
    assert list(task.logs) == ["hola"]


def test_failure_keeps_error() -> None:
    task = TaskRegistry().start("x", "X", lambda _ctx: 1 / 0)
    task.join(2)
    assert task.status is TaskStatus.FAILED
    assert "division" in (task.error or "")


def test_one_task_per_kind() -> None:
    reg = TaskRegistry()
    release = threading.Event()
    task = reg.start("engine", "Arrancar", lambda _ctx: release.wait(2))
    with pytest.raises(TaskBusyError):
        reg.start("engine", "Otra", lambda _ctx: None)
    reg.start("download", "Descargar", lambda _ctx: None).join(2)  # otro tipo sí
    release.set()
    task.join(2)
    reg.start("engine", "De nuevo", lambda _ctx: None).join(2)


def test_cancel_marks_cancelled() -> None:
    reg = TaskRegistry()
    started = threading.Event()

    def work(ctx: TaskContext) -> None:
        started.set()
        ctx.cancel.wait(2)
        ctx.check_cancelled()

    task = reg.start("generate", "G", work)
    started.wait(2)
    task.cancel()
    task.join(2)
    assert task.status is TaskStatus.CANCELLED


def test_take_finished_once() -> None:
    reg = TaskRegistry()
    reg.start("a", "A", lambda _ctx: None).join(2)
    assert [t.kind for t in reg.take_finished()] == ["a"]
    assert reg.take_finished() == []
