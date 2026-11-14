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
    parse_size,
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

    def stop_detached(self) -> None:
        self.calls.append("stop_detached")
        self.running = False

    def engine_path(self, host_path: Path) -> str:
        return str(host_path)

    def run_tool(self, args: list[str], timeout: float = 3600) -> str:
        return ""

    def parts_installed(self) -> bool:
        return False

    def install_parts(self, log: LogFn, cancel: threading.Event | None = None) -> None:
        pass

    def engine_memory(self) -> int | None:
        return None


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


def test_status_ready_by_http_even_without_docker() -> None:
    state, _msg = EngineManager(FakeRunner(available=False), FakeClient(0)).status()
    assert state is EngineState.READY


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
    runner = DockerRunner(Workspace(tmp_path), docker="docker")
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


def test_engine_path_maps_into_container(tmp_path: Path) -> None:
    runner = DockerRunner(Workspace(tmp_path))
    target = tmp_path / "engine" / "jobs" / "x" / "model.glb"
    assert runner.engine_path(target) == "/workspace/jobs/x/model.glb"
    with pytest.raises(ValueError):
        runner.engine_path(tmp_path / "otra" / "cosa")


def test_install_checks_accept_container_only_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El venv apunta a /workspace/... (solo existe en el contenedor): el enlace basta."""
    ws = Workspace(tmp_path)
    for venv in (ws.engine_dir / ".venv", ws.engine_dir / "parts" / ".venv"):
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "python").symlink_to("/workspace/.cache/python/no-existe/python3")
    ws.comfy_dir.mkdir(parents=True)
    (ws.comfy_dir / "main.py").write_text("")
    model = ws.engine_dir / "parts" / "Hunyuan3D-Part" / "P3-SAM" / "model.py"
    model.parent.mkdir(parents=True)
    model.write_text("")
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **_k: subprocess.CompletedProcess(cmd, 0, "", "")
    )
    runner = DockerRunner(ws)
    assert runner.is_installed()
    assert runner.parts_installed()


def test_parse_size() -> None:
    assert parse_size("2.5GiB") == int(2.5 * 1024**3)
    assert parse_size(" 512MiB ") == 512 * 1024**2
    assert parse_size("raro") is None


def test_stop_reports_when_engine_cannot_be_controlled() -> None:
    runner = FakeRunner(running=False)  # Docker no ve el contenedor...
    manager = EngineManager(runner, FakeClient(0))  # ...pero el motor responde
    with pytest.raises(EngineError, match="no se pudo detener"):
        manager.stop()


def test_compose_inside_steam_container_uses_host_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from threedai_modeler.core import engine

    monkeypatch.setattr(engine.Path, "exists", lambda self: str(self) == engine.HOST_COMPOSE)
    assert engine.find_compose("/run/host/usr/bin/docker") == [engine.HOST_COMPOSE]
    assert engine.find_compose("/usr/bin/docker") == ["/usr/bin/docker", "compose"]
