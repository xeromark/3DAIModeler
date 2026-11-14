import json
import socket
import struct

import pytest

from threedai_modeler.core.comfy import (
    ComfyClient,
    ComfyError,
    Event,
    ExecutionFailedError,
    ExecutionInterruptedError,
    PromptRejectedError,
    WebSocket,
    ws_accept_key,
)


def frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    b0 = (0x80 if fin else 0) | opcode
    n = len(payload)
    if n < 126:
        return struct.pack("!BB", b0, n) + payload
    if n < 1 << 16:
        return struct.pack("!BBH", b0, 126, n) + payload
    return struct.pack("!BBQ", b0, 127, n) + payload


@pytest.fixture
def pair() -> tuple[WebSocket, socket.socket]:
    client, server = socket.socketpair()
    return WebSocket(client), server


def test_accept_key_rfc_example() -> None:
    assert ws_accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_text_and_binary(pair: tuple[WebSocket, socket.socket]) -> None:
    ws, server = pair
    server.sendall(frame(0x2, b"\x89PNG") + frame(0x1, b'{"type":"status"}'))
    assert ws.recv_text(timeout=1) == '{"type":"status"}'


def test_large_and_fragmented(pair: tuple[WebSocket, socket.socket]) -> None:
    ws, server = pair
    big = json.dumps({"x": "a" * 70_000}).encode()
    server.sendall(frame(0x1, big[:10], fin=False) + frame(0x0, big[10:]))
    assert ws.recv_text(timeout=1) == big.decode()


def test_ping_gets_masked_pong(pair: tuple[WebSocket, socket.socket]) -> None:
    ws, server = pair
    server.sendall(frame(0x9, b"hi") + frame(0x1, b"ok"))
    assert ws.recv_text(timeout=1) == "ok"
    pong = server.recv(64)
    assert pong[0] == 0x8A and pong[1] == 0x80 | 2
    mask, body = pong[2:6], pong[6:8]
    assert bytes(b ^ mask[i % 4] for i, b in enumerate(body)) == b"hi"


def test_timeout_keeps_partial_frame(pair: tuple[WebSocket, socket.socket]) -> None:
    ws, server = pair
    data = frame(0x1, b"hola")
    server.sendall(data[:3])
    assert ws.recv_text(timeout=0.1) is None
    server.sendall(data[3:])
    assert ws.recv_text(timeout=1) == "hola"


def test_close_raises(pair: tuple[WebSocket, socket.socket]) -> None:
    ws, server = pair
    server.sendall(frame(0x8, b""))
    with pytest.raises(ComfyError):
        ws.recv_text(timeout=1)


def test_event_errors() -> None:
    with pytest.raises(ExecutionFailedError, match="shape_sampler"):
        ComfyClient._check_event(
            Event(
                "execution_error",
                {"node_id": "shape_sampler", "node_type": "KSampler", "exception_message": "OOM"},
            )
        )
    with pytest.raises(ExecutionInterruptedError):
        ComfyClient._check_event(Event("execution_interrupted", {}))
    ComfyClient._check_event(Event("progress", {"value": 1, "max": 2}))


def test_rejected_prompt_lists_node_errors() -> None:
    detail = json.dumps(
        {
            "error": {"message": "Prompt outputs failed validation"},
            "node_errors": {
                "unet": {"class_type": "UNETLoader", "errors": [{"details": "file not found"}]}
            },
        }
    ).encode()
    with pytest.raises(PromptRejectedError) as info:
        ComfyClient._raise_rejected(detail)
    assert "unet (UNETLoader): file not found" in str(info.value)
    assert "unet" in info.value.node_errors
