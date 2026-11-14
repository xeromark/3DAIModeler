"""Ciclo de vida del motor: instalar, arrancar, detener y consultar estado.

`Runner` abstrae cómo se ejecuta el motor (hoy Docker; mañana nativo) para que el addon
no cambie (RNF-08). El estado siempre se detecta, nunca se supone.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from .comfy import ComfyClient
from .paths import Workspace

LogFn = Callable[[str], None]
IMAGE = "3daimodeler-engine:dev"
NO_DOCKER = "Docker no está instalado. Instálalo junto con nvidia-container-toolkit."
SERVICE = "engine"


class EngineError(RuntimeError):
    """Error accionable del motor (el mensaje dice qué hacer)."""


class EngineState(StrEnum):
    UNAVAILABLE = "unavailable"
    """Falta un requisito (p. ej. Docker no instalado o apagado)."""
    NOT_INSTALLED = "not_installed"
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"


class Runner(Protocol):
    def check_available(self) -> None:
        """Lanza EngineError con instrucciones si falta algún requisito."""

    def is_installed(self) -> bool: ...
    def is_running(self) -> bool: ...
    def install(self, log: LogFn, cancel: threading.Event | None = None) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def logs(self, lines: int = 200) -> str: ...
    def stop_detached(self) -> None:
        """Ordena detener sin esperar (para el cierre de Blender)."""


class DockerRunner:
    """Motor en Docker Compose (compose.yaml del workspace)."""

    def __init__(self, ws: Workspace, docker: str = "docker") -> None:
        self.ws = ws
        self.docker = docker

    # ------------------------------------------------------------ comandos

    def _env(self) -> dict[str, str]:
        return {**os.environ, "HOST_UID": str(os.getuid()), "HOST_GID": str(os.getgid())}

    def _compose(self, *args: str) -> list[str]:
        return [self.docker, "compose", "-f", str(self.ws.compose_file), *args]

    def _run(self, cmd: list[str], timeout: float = 60) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                cmd,
                cwd=self.ws.root,
                env=self._env(),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            raise EngineError(NO_DOCKER) from None
        except subprocess.TimeoutExpired:
            raise EngineError(f"Docker no respondió a tiempo: {' '.join(cmd[:4])}") from None

    def _checked(self, cmd: list[str], timeout: float = 120) -> str:
        result = self._run(cmd, timeout)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()[-5:]
            raise EngineError(f"Falló '{' '.join(cmd[1:4])}': " + " | ".join(detail))
        return result.stdout

    def _stream(self, cmd: list[str], log: LogFn, cancel: threading.Event | None) -> None:
        proc = subprocess.Popen(
            cmd,
            cwd=self.ws.root,
            env=self._env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                log(line.rstrip())
                if cancel is not None and cancel.is_set():
                    proc.terminate()
                    raise EngineError("Instalación cancelada")
        finally:
            code = proc.wait()
        if code != 0:
            raise EngineError(f"Falló '{' '.join(cmd[1:5])}' (código {code}); revisa el log")

    # -------------------------------------------------------------- Runner

    def check_available(self) -> None:
        if shutil.which(self.docker) is None:
            raise EngineError(NO_DOCKER)
        result = self._run([self.docker, "info", "--format", "{{.ServerVersion}}"], timeout=15)
        if result.returncode != 0:
            err = result.stderr.lower()
            if "permission denied" in err:
                raise EngineError(
                    "Tu usuario no puede usar Docker: agrégalo al grupo 'docker' y reinicia sesión."
                )
            raise EngineError("Docker está apagado: inícialo con 'sudo systemctl start docker'.")

    def is_installed(self) -> bool:
        venv_ok = (self.ws.engine_dir / ".venv" / "bin" / "python").exists()
        comfy_ok = (self.ws.comfy_dir / "main.py").is_file()
        if not (venv_ok and comfy_ok):
            return False
        return self._run([self.docker, "image", "inspect", IMAGE], timeout=15).returncode == 0

    def is_running(self) -> bool:
        out = self._run(self._compose("ps", "--status", "running", "-q", SERVICE), timeout=15)
        return out.returncode == 0 and bool(out.stdout.strip())

    def install(self, log: LogFn, cancel: threading.Event | None = None) -> None:
        self.ws.engine_dir.mkdir(exist_ok=True)
        log(">> Construyendo la imagen del motor")
        self._stream(self._compose("build", SERVICE), log, cancel)
        log(">> Instalando ComfyUI y PyTorch (varios GB, puede tardar)")
        self._stream(
            self._compose("run", "--rm", SERVICE, "bash", "/scripts/setup-engine.sh"), log, cancel
        )

    def start(self) -> None:
        self._checked(self._compose("up", "-d", SERVICE))

    def stop(self) -> None:
        self._checked(self._compose("stop", SERVICE), timeout=60)

    def logs(self, lines: int = 200) -> str:
        result = self._run(self._compose("logs", "--no-color", "--tail", str(lines), SERVICE))
        return result.stdout + result.stderr

    def stop_detached(self) -> None:
        subprocess.Popen(
            self._compose("stop", SERVICE),
            cwd=self.ws.root,
            env=self._env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


class EngineManager:
    def __init__(self, runner: Runner, client: ComfyClient) -> None:
        self.runner = runner
        self.client = client

    def status(self) -> tuple[EngineState, str]:
        """Estado actual y un mensaje para mostrar."""
        try:
            self.runner.check_available()
        except EngineError as err:
            return EngineState.UNAVAILABLE, str(err)
        if self.client.is_ready():
            return EngineState.READY, f"Listo en {self.client.base_url}"
        if self.runner.is_running():
            return EngineState.STARTING, "Arrancando..."
        if not self.runner.is_installed():
            return EngineState.NOT_INSTALLED, "El motor no está instalado"
        return EngineState.STOPPED, "Detenido"

    def install(self, log: LogFn, cancel: threading.Event | None = None) -> None:
        self.runner.check_available()
        self.runner.install(log, cancel)

    def start(self, wait: bool = True, timeout: float = 180.0) -> None:
        state, message = self.status()
        if state is EngineState.READY:
            return
        if state in (EngineState.UNAVAILABLE, EngineState.NOT_INSTALLED):
            raise EngineError(message)
        if state is EngineState.STOPPED:
            self.runner.start()
        if wait:
            self.wait_ready(timeout)

    def wait_ready(self, timeout: float = 180.0, poll: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.client.is_ready():
                return
            if not self.runner.is_running():
                raise EngineError("El motor se detuvo al arrancar; revisa los logs")
            time.sleep(poll)
        raise EngineError(f"El motor no respondió en {timeout:.0f} s; revisa los logs")

    def stop(self) -> None:
        if self.runner.is_running():
            self.runner.stop()

    def stop_detached(self) -> None:
        if self.runner.is_running():
            self.runner.stop_detached()


def default_manager(ws: Workspace, client: ComfyClient | None = None) -> EngineManager:
    return EngineManager(DockerRunner(ws), client or ComfyClient())


def engine_disk_usage(ws: Workspace) -> int:
    """Bytes usados por engine/ (puede tardar unos segundos: llamar fuera de la UI)."""
    total = 0
    for root, _dirs, files in os.walk(ws.engine_dir):
        for name in files:
            path = Path(root) / name
            if not path.is_symlink():
                total += path.stat().st_size
    return total
