"""Generación real de punta a punta. Requiere el motor corriendo y pixal3d-int8 descargado.

./scripts/dev.sh test -m integration
"""

from pathlib import Path

import pytest

from threedai_modeler.core.comfy import ComfyClient
from threedai_modeler.core.jobs import Job, StageStatus
from threedai_modeler.core.paths import Workspace
from threedai_modeler.core.pipeline import Pipeline
from threedai_modeler.core.presets import Presets
from threedai_modeler.core.registry import ModelState, Registry

pytestmark = pytest.mark.integration

SAMPLE = Path("engine/samples/viking_axe.png")


@pytest.fixture
def ws() -> Workspace:
    ws = Workspace.discover()
    if not ComfyClient().is_ready():
        pytest.skip("motor apagado: ./scripts/engine.sh up")
    if Registry.for_workspace(ws).state(ws, "pixal3d-int8") is not ModelState.INSTALLED:
        pytest.skip("pixal3d-int8 no descargado")
    if not (ws.root / SAMPLE).is_file():
        pytest.skip(f"falta {SAMPLE}")
    return ws


def test_draft_generation_produces_glb(ws: Workspace) -> None:
    registry = Registry.for_workspace(ws)
    presets = Presets.load(ws.presets_file)
    job = Job.create(
        ws, ws.root / SAMPLE, model_id="pixal3d-int8", quality="draft", target_faces=5000, seed=1
    )
    Pipeline(ws, registry, presets, ComfyClient()).run(job)

    record = Job.load(ws, job.id).stages["generate"]
    assert record.status is StageStatus.DONE
    glb = job.output_path("generate", "glb")
    assert glb is not None and glb.read_bytes()[:4] == b"glTF"
    assert record.metrics["seconds"] > 0
