import time

from threedai_modeler.core.comfy import ComfyClient
from threedai_modeler.core.monitor import ResourceMonitor
from threedai_modeler.core.usage import read_gpu, snapshot


def test_energy_is_integrated_from_power() -> None:
    with ResourceMonitor(power_w=lambda: 100.0, interval=0.05) as monitor:
        time.sleep(0.4)
    data = monitor.as_dict()
    expected_wh = 100.0 * monitor.seconds / 3600
    assert abs(data["energy_wh"] - expected_wh) < 0.005
    assert data["peak_power_w"] == 100.0


def test_no_energy_without_power_readings() -> None:
    with ResourceMonitor(power_w=lambda: None, interval=0.05) as monitor:
        time.sleep(0.1)
    assert "energy_wh" not in monitor.as_dict()


def test_read_gpu_handles_missing_tool() -> None:
    assert read_gpu(None) is None
    assert read_gpu("/no/existe/nvidia-smi") is None


class StatsClient(ComfyClient):
    def system_stats(self) -> dict[str, object]:
        return {
            "system": {"ram_total": 16 * 2**30, "ram_free": 6 * 2**30},
            "devices": [
                {"vram_total": 16 * 2**30, "vram_free": 7 * 2**30, "torch_vram_total": 5 * 2**30}
            ],
        }


def test_snapshot_from_engine_stats() -> None:
    snap = snapshot(StatsClient(), None)
    assert snap.vram_used == 9 * 2**30 and snap.engine_vram == 5 * 2**30
    assert snap.ram_used == 10 * 2**30
    assert snap.power_w is None
