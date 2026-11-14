"""Mide picos de RAM, swap y VRAM durante un trabajo (RNF-01)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

GiB = 1024**3

VramFn = Callable[[], float | None]
"""Devuelve la VRAM usada en bytes, o None si no se puede medir."""


def read_meminfo(path: Path = Path("/proc/meminfo")) -> dict[str, int]:
    """Valores de /proc/meminfo en bytes."""
    values: dict[str, int] = {}
    for line in path.read_text().splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            values[key] = int(parts[0]) * (1024 if len(parts) > 1 else 1)
    return values


class ResourceMonitor:
    def __init__(self, vram_used: VramFn | None = None, interval: float = 1.0) -> None:
        self._vram_used = vram_used
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self.peak_ram = 0
        self.peak_swap = 0
        self.peak_vram = 0.0
        self._start = 0.0

    def _sample(self) -> None:
        mem = read_meminfo()
        self.peak_ram = max(self.peak_ram, mem["MemTotal"] - mem["MemAvailable"])
        self.peak_swap = max(self.peak_swap, mem["SwapTotal"] - mem["SwapFree"])
        if self._vram_used is not None:
            try:
                used = self._vram_used()
            except Exception:  # medir nunca debe romper un trabajo
                used = None
            if used is not None:
                self.peak_vram = max(self.peak_vram, used)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self._sample()

    def __enter__(self) -> ResourceMonitor:
        self._start = time.monotonic()
        self._sample()
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join()
        self._sample()
        self.seconds = time.monotonic() - self._start

    seconds = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "seconds": round(self.seconds, 1),
            "peak_ram_gib": round(self.peak_ram / GiB, 2),
            "peak_swap_gib": round(self.peak_swap / GiB, 2),
            "peak_vram_gib": round(self.peak_vram / GiB, 2),
        }
