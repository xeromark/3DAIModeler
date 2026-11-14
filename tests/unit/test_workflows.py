from pathlib import Path

import pytest

from threedai_modeler.core.presets import PresetError, Presets
from threedai_modeler.core.registry import Registry
from threedai_modeler.core.workflows import (
    GenerationParams,
    WorkflowError,
    build_workflow,
    load_template,
    set_input,
    stage_label,
)

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / "workflows"


@pytest.fixture
def presets() -> Presets:
    return Presets.load(ROOT / "models" / "presets.toml")


@pytest.fixture
def registry() -> Registry:
    return Registry.load(ROOT / "models" / "registry.toml")


def test_presets_load(presets: Presets) -> None:
    assert set(presets.quality) == {"draft", "standard", "max"}
    assert presets.resolve_faces("medium") == 50_000
    assert presets.resolve_faces("35_000") == 35_000
    assert presets.resolve_faces(1234) == 1234
    assert presets.resolve_faces("none") == 0
    with pytest.raises(PresetError):
        presets.resolve_faces("enorme")
    with pytest.raises(PresetError):
        presets.resolve_faces(10)


@pytest.mark.parametrize("family", ["pixal3d", "trellis2", "pixal3d-multiview"])
def test_template_links_point_to_existing_nodes(family: str) -> None:
    wf = load_template(WORKFLOWS, family, "image_to_mesh")
    for node_id, node in wf.items():
        assert "class_type" in node, node_id
        for name, value in node["inputs"].items():
            if isinstance(value, list):
                assert value[0] in wf, f"{node_id}.{name} -> {value[0]} no existe"


FAMILY_MODELS = [
    ("pixal3d", "pixal3d-int8"),
    ("trellis2", "trellis2-int8"),
    ("pixal3d-multiview", "pixal3d-multiview-int8"),
]


@pytest.mark.parametrize(("family", "model_id"), FAMILY_MODELS)
def test_template_uses_registry_files(registry: Registry, family: str, model_id: str) -> None:
    """Cada archivo que carga la plantilla debe estar en el registro del modelo."""
    wf = load_template(WORKFLOWS, family, "image_to_mesh")
    names = {f.filename for f in registry.files_of(model_id)}
    loader_inputs = ("bg_removal_name", "model_name", "clip_name", "vae_name", "unet_name")
    for node in wf.values():
        for key in loader_inputs:
            if key in node["inputs"]:
                assert node["inputs"][key] in names


def test_build_pixal3d_injects_params(registry: Registry, presets: Presets) -> None:
    params = GenerationParams(
        image="threedai/a.png",
        model_id="pixal3d-bf16",
        quality=presets.get_quality("max"),
        target_faces=12_345,
        seed=7,
    )
    wf = build_workflow(WORKFLOWS, registry, params)
    assert wf["load_image"]["inputs"]["image"] == "threedai/a.png"
    assert wf["unet"]["inputs"]["unet_name"] == "pixal3d_bf16.safetensors"
    assert wf["decimate"]["inputs"]["target_face_count"] == 12_345
    assert wf["upsample_stage"]["inputs"]["target_resolution"] == 2048
    assert wf["structure_sampler"]["inputs"]["seed"] == 7
    assert wf["texture_sampler"]["inputs"]["seed"] == 8
    # La plantilla original no se modifica.
    assert load_template(WORKFLOWS, "pixal3d", "image_to_mesh")["unet"]["inputs"]["unet_name"] == (
        "pixal3d_int8_convrot.safetensors"
    )


def test_set_input_rejects_links_and_unknown() -> None:
    wf = {"a": {"class_type": "X", "inputs": {"v": 1, "link": ["b", 0]}}}
    with pytest.raises(WorkflowError):
        set_input(wf, "a", "link", 3)
    with pytest.raises(WorkflowError):
        set_input(wf, "a", "nope", 3)
    with pytest.raises(WorkflowError):
        set_input(wf, "zzz", "v", 3)


def test_views_require_multiview_model(registry: Registry, presets: Presets) -> None:
    params = GenerationParams(
        "a.png", "pixal3d-int8", presets.get_quality("draft"), 1000, 1, views={"back": "b.png"}
    )
    with pytest.raises(WorkflowError, match="multivista"):
        build_workflow(WORKFLOWS, registry, params)


def test_multiview_keeps_only_given_views(registry: Registry, presets: Presets) -> None:
    params = GenerationParams(
        "f.png",
        "pixal3d-multiview-int8",
        presets.get_quality("draft"),
        1000,
        1,
        views={"back": "b.png"},
    )
    wf = build_workflow(WORKFLOWS, registry, params)
    cond = wf["conditioning"]["inputs"]
    assert set(cond) == {"clip_vision_model", "fov", "front", "back"}
    assert wf["load_image_front"]["inputs"]["image"] == "f.png"
    assert wf["load_image_back"]["inputs"]["image"] == "b.png"
    assert "load_image_left" not in wf and "crop_right" not in wf
    for node in wf.values():
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in wf


def test_multiview_rejects_unknown_view(registry: Registry, presets: Presets) -> None:
    params = GenerationParams(
        "f.png", "pixal3d-multiview-int8", presets.get_quality("draft"), 1, 1, views={"top": "t"}
    )
    with pytest.raises(WorkflowError, match="desconocidas"):
        build_workflow(WORKFLOWS, registry, params)


def test_trellis2_has_no_camera_estimation(registry: Registry, presets: Presets) -> None:
    params = GenerationParams("a.png", "trellis2-int8", presets.get_quality("draft"), 1000, 1)
    wf = build_workflow(WORKFLOWS, registry, params)
    assert wf["conditioning"]["class_type"] == "Trellis2Conditioning"
    assert "moge" not in wf
    assert wf["unet"]["inputs"]["unet_name"] == "trellis_2_int8_convrot.safetensors"


def test_stage_labels() -> None:
    assert stage_label("shape_sampler") == "Forma"
    assert stage_label("bake_normal") == "Horneando texturas"
    assert stage_label("algo_nuevo") == "algo_nuevo"


def test_no_reduction_skips_decimate(registry: Registry, presets: Presets) -> None:
    params = GenerationParams("a.png", "pixal3d-int8", presets.get_quality("draft"), 0, 1)
    wf = build_workflow(WORKFLOWS, registry, params)
    assert "decimate" not in wf
    assert wf["smooth_low"]["inputs"]["mesh"] == ["remesh", 0]
    for node in wf.values():  # ninguna conexión colgando
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in wf
