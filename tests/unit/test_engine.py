import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from threedai_modeler.core.comfy import ComfyClient
from threedai_modeler.core.engine import (
    DockerRunner,
    EngineError,
    EngineManager,
    EngineState,
    LogFn,
)
from threedai_modeler.core.paths import Workspace


class FakeRunner:
    def __init__(self, available: bool = True, installed: bool = True, running: bool = False):
        self.available = available
        self.installed = installed
        self.running = running
        self.calls: list[str] = []

    def check_available(self) -> None:
        if not self.available:
            raise EngineError("Docker está apagado")

    def is_installed(self) -> bool:
        return self.installed

    def is_running(self) -> bool:
        return self.running

    def install(self, log: LogFn, cancel: threading.Event | None = None) -> None:
        self.calls.append("install")
        self.installed = True

    def start(self) -> None:
        self.calls.append("start")
        self.running = True

    def stop(self) -> None:
        self.calls.append("stop")
        self.running = False

    def logs(self, lines: int = 200) -> str:
        return ""


class FakeClient(ComfyClient):
    def __init__(self, ready_after: int = 0) -> None:
        super().__init__()
        self.checks = 0
        self.ready_after = ready_after

    def is_ready(self) -> bool:
        self.checks += 1
        return self.ready_after >= 0 and self.checks > self.ready_after


def test_status_unavailable() -> None:
    state, msg = EngineManager(FakeRunner(available=False), FakeClient(-1)).status()
    assert state is EngineState.UNAVAILABLE and "apagado" in msg


def test_status_not_installed_stopped_starting_ready() -> None:
    assert EngineManager(FakeRunner(installed=False), FakeClient(-1)).status()[0] is (
        EngineState.NOT_INSTALLED
    )
    assert EngineManager(FakeRunner(), FakeClient(-1)).status()[0] is EngineState.STOPPED
    assert EngineManager(FakeRunner(running=True), FakeClient(-1)).status()[0] is (
        EngineState.STARTING
    )
    assert EngineManager(FakeRunner(running=True), FakeClient(0)).status()[0] is EngineState.READY


def test_start_waits_until_ready() -> None:
    runner = FakeRunner()
    manager = EngineManager(runner, FakeClient(ready_after=3))
    manager.start(wait=True, timeout=5)
    assert runner.calls == ["start"]


def test_start_refuses_when_not_installed() -> None:
    manager = EngineManager(FakeRunner(installed=False), FakeClient(-1))
    with pytest.raises(EngineError, match="no está instalado"):
        manager.start()


def test_wait_ready_detects_crash() -> None:
    runner = FakeRunner(running=False)
    with pytest.raises(EngineError, match="se detuvo"):
        EngineManager(runner, FakeClient(-1)).wait_ready(timeout=2, poll=0.01)


def test_stop_only_when_running() -> None:
    runner = FakeRunner(running=False)
    EngineManager(runner, FakeClient(-1)).stop()
    assert runner.calls == []


def test_docker_runner_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        assert kwargs["env"]["HOST_UID"].isdigit()
        assert kwargs["cwd"] == tmp_path
        return subprocess.CompletedProcess(cmd, 0, "abc123\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = DockerRunner(Workspace(tmp_path))
    assert runner.is_running()
    runner.start()
    runner.stop()
    compose = ["docker", "compose", "-f", str(tmp_path / "compose.yaml")]
    assert seen[0] == [*compose, "ps", "--status", "running", "-q", "engine"]
    assert seen[1] == [*compose, "up", "-d", "engine"]
    assert seen[2] == [*compose, "stop", "engine"]


def test_docker_runner_missing_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_a: Any, **_k: Any) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(EngineError, match="no está instalado"):
        DockerRunner(Workspace(tmp_path)).is_running()
