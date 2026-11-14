"""Uso de recursos en vivo: VRAM, RAM y potencia de la GPU.

La potencia (`nvidia-smi`) también se integra durante cada trabajo para guardar la energía (Wh)
en sus métricas de benchmark.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .comfy import ComfyClient, ComfyError

HOST_NVIDIA_SMI = "/run/host/usr/bin/nvidia-smi"
"""Dentro del contenedor de Steam el /usr del sistema está en /run/host."""


@dataclass(frozen=True)
class GpuReading:
    power_w: float
    utilization: float
    vram_used: int
    vram_total: int


@dataclass(frozen=True)
class Snapshot:
    vram_used: int | None
    vram_total: int | None
    engine_vram: int | None
    """Memoria de la GPU reservada por el motor (PyTorch)."""
    ram_used: int | None
    ram_total: int | None
    power_w: float | None
    gpu_util: float | None


def find_nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    return HOST_NVIDIA_SMI if Path(HOST_NVIDIA_SMI).exists() else None


def read_gpu(nvidia_smi: str | None) -> GpuReading | None:
    if nvidia_smi is None:
        return None
    query = "power.draw,utilization.gpu,memory.used,memory.total"
    try:
        out = subprocess.run(
            [nvidia_smi, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
        power, util, used, total = (float(x) for x in out.splitlines()[0].split(","))
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    mib = 1024 * 1024
    return GpuReading(power, util, int(used * mib), int(total * mib))


def snapshot(client: ComfyClient, nvidia_smi: str | None) -> Snapshot:
    vram_used = vram_total = engine_vram = ram_used = ram_total = None
    try:
        stats = client.system_stats()
        device = stats["devices"][0]
        vram_total = int(device["vram_total"])
        vram_used = vram_total - int(device["vram_free"])
        engine_vram = int(device.get("torch_vram_total", 0))
        ram_total = int(stats["system"]["ram_total"])
        ram_used = ram_total - int(stats["system"]["ram_free"])
    except (ComfyError, KeyError, IndexError, TypeError, ValueError):
        pass
    gpu = read_gpu(nvidia_smi)
    if gpu is not None and vram_used is None:
        vram_used, vram_total = gpu.vram_used, gpu.vram_total
    return Snapshot(
        vram_used,
        vram_total,
        engine_vram,
        ram_used,
        ram_total,
        gpu.power_w if gpu else None,
        gpu.utilization if gpu else None,
    )
