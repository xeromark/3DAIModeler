"""Cliente de ComfyUI (HTTP + WebSocket) usando solo la biblioteca estándar."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8188
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class ComfyError(RuntimeError):
    """Error general del motor."""


class PromptRejectedError(ComfyError):
    """ComfyUI rechazó el workflow (validación)."""

    def __init__(self, message: str, node_errors: dict[str, Any]) -> None:
        super().__init__(message)
        self.node_errors = node_errors


class ExecutionFailedError(ComfyError):
    """Un nodo falló durante la ejecución."""

    def __init__(self, node: str, node_type: str, message: str) -> None:
        super().__init__(f"{node} ({node_type}): {message}")
        self.node = node
        self.node_type = node_type


class ExecutionInterruptedError(ComfyError):
    """La ejecución fue interrumpida (cancelada)."""


@dataclass(frozen=True)
class Event:
    """Mensaje del WebSocket de ComfyUI, p. ej. type='progress', data={'value', 'max', 'node'}."""

    type: str
    data: dict[str, Any]


EventFn = Callable[[Event], None]


# --------------------------------------------------------------- WebSocket


def ws_accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()


class WebSocket:
    """Cliente WebSocket mínimo (RFC 6455): solo lectura de texto, responde pings."""

    def __init__(self, sock: socket.socket, buffered: bytes = b"") -> None:
        self._sock = sock
        self._buf = buffered
        self._fragments: list[bytes] = []
        self._message_opcode = 0x1

    @classmethod
    def connect(cls, host: str, port: int, path: str, timeout: float = 10.0) -> WebSocket:
        sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode())
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise ComfyError("WebSocket: conexión cerrada durante el handshake")
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            raise ComfyError(f"WebSocket: respuesta inesperada {lines[0]!r}")
        pairs = (ln.partition(":") for ln in lines[1:])
        headers = {k.strip().lower(): v.strip() for k, _, v in pairs}
        if headers.get("sec-websocket-accept") != ws_accept_key(key):
            raise ComfyError("WebSocket: Sec-WebSocket-Accept inválido")
        return cls(sock, rest)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._send(0x8, b"")
        self._sock.close()

    def _send(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 1 << 16:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(header + mask + masked)

    def _try_parse(self) -> tuple[bool, int, bytes] | None:
        """Extrae un frame completo del buffer o None si falta data."""
        buf = self._buf
        if len(buf) < 2:
            return None
        b0, b1 = buf[0], buf[1]
        fin, opcode, masked, n = bool(b0 & 0x80), b0 & 0x0F, bool(b1 & 0x80), b1 & 0x7F
        pos = 2
        if n == 126:
            if len(buf) < 4:
                return None
            (n,) = struct.unpack("!H", buf[2:4])
            pos = 4
        elif n == 127:
            if len(buf) < 10:
                return None
            (n,) = struct.unpack("!Q", buf[2:10])
            pos = 10
        mask = b""
        if masked:
            if len(buf) < pos + 4:
                return None
            mask, pos = buf[pos : pos + 4], pos + 4
        if len(buf) < pos + n:
            return None
        payload = buf[pos : pos + n]
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._buf = buf[pos + n :]
        return fin, opcode, payload

    def recv_text(self, timeout: float) -> str | None:
        """Devuelve el próximo mensaje de texto, o None si no llega nada en `timeout`.

        Los mensajes binarios (previews de imágenes) se descartan.
        """
        deadline = time.monotonic() + timeout
        while True:
            frame = self._try_parse()
            if frame is not None:
                message = self._handle_frame(*frame)
                if message is not None:
                    return message
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(65536)
            except TimeoutError:
                return None
            if not chunk:
                raise ComfyError("WebSocket: conexión cerrada")
            self._buf += chunk

    def _handle_frame(self, fin: bool, opcode: int, payload: bytes) -> str | None:
        if opcode == 0x9:  # ping
            self._send(0xA, payload)
            return None
        if opcode == 0x8:
            raise ComfyError("WebSocket: el servidor cerró la conexión")
        if opcode in (0x1, 0x2):  # inicio de mensaje
            self._message_opcode = opcode
            self._fragments = [payload]
        elif opcode == 0x0:  # continuación
            self._fragments.append(payload)
        else:
            return None
        if not fin:
            return None
        data = b"".join(self._fragments)
        self._fragments = []
        return data.decode("utf-8") if self._message_opcode == 0x1 else None


# ------------------------------------------------------------------ Cliente


class ComfyClient:
    def __init__(
        self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 30.0
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # -------------------------------------------------------------- HTTP

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> bytes:
        req = urllib.request.Request(
            self.base_url + path, data=body, method=method, headers=headers or {}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                data: bytes = resp.read()
                return data
        except urllib.error.HTTPError as err:
            detail = err.read()
            if path == "/prompt" and err.code == 400:
                self._raise_rejected(detail)
            raise ComfyError(f"{method} {path}: HTTP {err.code} {detail[:500]!r}") from err
        except (urllib.error.URLError, OSError) as err:
            raise ComfyError(f"Motor no disponible en {self.base_url}: {err}") from err

    def _json(self, method: str, path: str, payload: Any = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"} if body is not None else None
        raw = self._request(method, path, body, headers)
        return json.loads(raw) if raw else None

    @staticmethod
    def _raise_rejected(detail: bytes) -> None:
        try:
            info = json.loads(detail)
        except json.JSONDecodeError:
            raise PromptRejectedError(detail.decode(errors="replace"), {}) from None
        error = info.get("error", {})
        node_errors = info.get("node_errors", {})
        lines = [error.get("message", "workflow inválido"), error.get("details", "")]
        for node, ne in node_errors.items():
            for e in ne.get("errors", []):
                reason = e.get("details") or e.get("message")
                lines.append(f"  {node} ({ne.get('class_type')}): {reason}")
        raise PromptRejectedError("\n".join(x for x in lines if x), node_errors)

    def is_ready(self) -> bool:
        try:
            self._request("GET", "/system_stats", timeout=3)
        except ComfyError:
            return False
        return True

    def system_stats(self) -> dict[str, Any]:
        result: dict[str, Any] = self._json("GET", "/system_stats")
        return result

    def upload_image(self, path: Path, name: str | None = None, subfolder: str = "threedai") -> str:
        """Sube una imagen a input/ del motor. Devuelve el nombre para LoadImage."""
        boundary = uuid.uuid4().hex
        filename = name or path.name
        disposition = f"--{boundary}\r\nContent-Disposition: form-data; name="
        parts = [
            f'{disposition}"subfolder"\r\n\r\n{subfolder}\r\n',
            f'{disposition}"overwrite"\r\n\r\ntrue\r\n',
            f'{disposition}"image"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n",
        ]
        body = "".join(parts).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        raw = self._request(
            "POST",
            "/upload/image",
            body,
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        info = json.loads(raw)
        sub = info.get("subfolder")
        return f"{sub}/{info['name']}" if sub else str(info["name"])

    def queue(self, workflow: dict[str, Any], client_id: str) -> str:
        result = self._json("POST", "/prompt", {"prompt": workflow, "client_id": client_id})
        return str(result["prompt_id"])

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        result = self._json("GET", f"/history/{urllib.parse.quote(prompt_id)}")
        entry: dict[str, Any] | None = (result or {}).get(prompt_id)
        return entry

    def interrupt(self) -> None:
        self._json("POST", "/interrupt", {})

    def free(self, unload_models: bool = True, free_memory: bool = True) -> None:
        """Pide al motor que descargue modelos de VRAM/RAM."""
        self._json("POST", "/free", {"unload_models": unload_models, "free_memory": free_memory})

    def download(self, file_info: dict[str, Any], dest: Path) -> Path:
        """Descarga un archivo de salida (dict con filename/subfolder/type) a `dest`."""
        query = urllib.parse.urlencode(
            {
                "filename": file_info["filename"],
                "subfolder": file_info.get("subfolder", ""),
                "type": file_info.get("type", "output"),
            }
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._request("GET", f"/view?{query}", timeout=300))
        return dest

    # --------------------------------------------------------- ejecución

    def run(
        self,
        workflow: dict[str, Any],
        on_event: EventFn | None = None,
        cancel: threading.Event | None = None,
        history_poll: float = 5.0,
    ) -> dict[str, Any]:
        """Ejecuta un workflow y espera el resultado. Devuelve las salidas por nodo.

        Los eventos (progress, executing, executed...) se pasan a `on_event`.
        Si `cancel` se activa, interrumpe la ejecución y lanza ExecutionInterruptedError.
        """
        client_id = uuid.uuid4().hex
        ws = WebSocket.connect(self.host, self.port, f"/ws?clientId={client_id}")
        try:
            prompt_id = self.queue(workflow, client_id)
            next_poll = time.monotonic() + history_poll
            interrupted = False
            while True:
                if cancel is not None and cancel.is_set() and not interrupted:
                    self.interrupt()
                    interrupted = True
                text = ws.recv_text(timeout=1.0)
                if text is not None:
                    msg = json.loads(text)
                    event = Event(str(msg.get("type")), msg.get("data") or {})
                    if event.data.get("prompt_id") not in (None, prompt_id):
                        continue
                    if on_event:
                        on_event(event)
                    self._check_event(event)
                    if event.type == "execution_success":
                        break
                    if event.type == "executing" and event.data.get("node") is None:
                        break
                if time.monotonic() >= next_poll:
                    next_poll = time.monotonic() + history_poll
                    entry = self.history(prompt_id)
                    if entry and entry.get("status", {}).get("completed") is not None:
                        break
        finally:
            ws.close()
        return self._final_outputs(prompt_id)

    @staticmethod
    def _check_event(event: Event) -> None:
        if event.type == "execution_error":
            d = event.data
            raise ExecutionFailedError(
                str(d.get("node_id")), str(d.get("node_type")), str(d.get("exception_message"))
            )
        if event.type == "execution_interrupted":
            raise ExecutionInterruptedError("ejecución interrumpida")

    def _final_outputs(self, prompt_id: str, wait: float = 60.0) -> dict[str, Any]:
        # ComfyUI avisa "terminado" por WebSocket un instante antes de guardar el historial;
        # con el sistema cargado (swap) esa demora puede ser de varios segundos.
        deadline = time.monotonic() + wait
        entry = self.history(prompt_id)
        while entry is None and time.monotonic() < deadline:
            time.sleep(0.5)
            entry = self.history(prompt_id)
        if entry is None:
            raise ComfyError(f"{prompt_id}: el motor no guardó el resultado en {wait:.0f} s")
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            for kind, data in status.get("messages", []):
                if kind == "execution_error":
                    self._check_event(Event(kind, data))
                if kind == "execution_interrupted":
                    raise ExecutionInterruptedError("ejecución interrumpida")
            raise ComfyError(f"{prompt_id}: falló sin detalle")
        outputs: dict[str, Any] = entry.get("outputs", {})
        return outputs
