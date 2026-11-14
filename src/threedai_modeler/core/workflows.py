"""Adaptadores: convierten parámetros de generación en un workflow de ComfyUI.

Cada familia de modelos tiene una plantilla en workflows/<familia>/ con IDs de nodo
legibles; los parámetros se inyectan por (nodo, entrada), nunca por posición.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .presets import Quality
from .registry import Registry

Workflow = dict[str, Any]


class WorkflowError(ValueError):
    """Plantilla inválida o familia no soportada."""


@dataclass(frozen=True)
class GenerationParams:
    image: str
    """Nombre de la imagen ya subida al motor (resultado de upload_image)."""
    model_id: str
    quality: Quality
    target_faces: int
    """0 = sin reducir."""
    seed: int
    views: dict[str, str] = field(default_factory=dict)
    """Vistas adicionales ya subidas al motor: left/back/right -> nombre (solo multivista)."""


# Etiquetas para mostrar el progreso: prefijo del ID de nodo -> etapa visible.
STAGE_LABELS: tuple[tuple[str, str], ...] = (
    ("load_image", "Preparando imagen"),
    ("crop_", "Preparando imagen"),
    ("bg_", "Quitando fondo"),
    ("crop", "Preparando imagen"),
    ("moge", "Estimando cámara"),
    ("fov", "Estimando cámara"),
    ("clip_vision", "Cargando modelos"),
    ("conditioning", "Analizando imagen"),
    ("unet", "Cargando modelos"),
    ("shape_vae", "Cargando modelos"),
    ("texture_vae", "Cargando modelos"),
    ("structure", "Estructura"),
    ("shape", "Forma"),
    ("upsample", "Detalle"),
    ("texture", "Textura"),
    ("remesh", "Malla"),
    ("decimate", "Reduciendo polígonos"),
    ("smooth", "Malla"),
    ("unwrap", "UVs"),
    ("bake", "Horneando texturas"),
    ("apply_textures", "Materiales"),
    ("save", "Guardando"),
)


def stage_label(node_id: str) -> str:
    for prefix, label in STAGE_LABELS:
        if node_id.startswith(prefix):
            return label
    return node_id


def load_template(workflows_dir: Path, family: str, name: str) -> Workflow:
    path = workflows_dir / family / f"{name}.json"
    if not path.is_file():
        raise WorkflowError(f"no existe la plantilla {path}")
    data: Workflow = json.loads(path.read_text())
    return data


def set_input(wf: Workflow, node: str, name: str, value: Any) -> None:
    try:
        inputs = wf[node]["inputs"]
    except KeyError:
        raise WorkflowError(f"la plantilla no tiene el nodo '{node}'") from None
    if name not in inputs:
        raise WorkflowError(f"el nodo '{node}' no tiene la entrada '{name}'")
    if isinstance(inputs[name], list):
        raise WorkflowError(f"'{node}.{name}' es una conexión, no un valor")
    inputs[name] = value


def _diffusion_file(registry: Registry, model_id: str) -> str:
    names = [f.filename for f in registry.files_of(model_id) if f.dest == "diffusion_models"]
    if len(names) != 1:
        raise WorkflowError(f"{model_id}: se esperaba un archivo en diffusion_models")
    return names[0]


VIEWS = ("front", "left", "back", "right")
EXTRA_VIEWS = VIEWS[1:]
MULTIVIEW_FAMILY = "pixal3d-multiview"


def build_trellis_family(template: Workflow, registry: Registry, p: GenerationParams) -> Workflow:
    """Pixal3D y TRELLIS.2 comparten pipeline; solo cambian el acondicionamiento y el modelo."""
    wf = copy.deepcopy(template)
    set_input(wf, "load_image", "image", p.image)
    _apply_common(wf, registry, p)
    return wf


def build_multiview(template: Workflow, registry: Registry, p: GenerationParams) -> Workflow:
    """Pixal3D multivista: frente obligatorio; izquierda, espalda y derecha opcionales."""
    unknown = set(p.views) - set(EXTRA_VIEWS)
    if unknown:
        raise WorkflowError(f"vistas desconocidas: {sorted(unknown)}")
    wf = copy.deepcopy(template)
    images = {"front": p.image, **p.views}
    for view in VIEWS:
        if view in images:
            set_input(wf, f"load_image_{view}", "image", images[view])
        else:  # vista no entregada: fuera del grafo
            for node in (f"load_image_{view}", f"bg_remove_{view}", f"crop_{view}"):
                del wf[node]
            del wf["conditioning"]["inputs"][view]
    _apply_common(wf, registry, p)
    return wf


def _apply_common(wf: Workflow, registry: Registry, p: GenerationParams) -> None:
    q = p.quality
    set_input(wf, "unet", "unet_name", _diffusion_file(registry, p.model_id))

    set_input(wf, "structure_sampler", "seed", p.seed)
    set_input(wf, "shape_sampler", "seed", p.seed)
    set_input(wf, "upsample_sampler", "seed", p.seed)
    set_input(wf, "texture_sampler", "seed", p.seed + 1)

    set_input(wf, "structure_sampler", "steps", q.structure_steps)
    set_input(wf, "shape_sampler", "steps", q.shape_steps)
    set_input(wf, "upsample_sampler", "steps", q.upsample_steps)
    set_input(wf, "texture_sampler", "steps", q.texture_steps)

    set_input(wf, "upsample_stage", "target_resolution", q.upsample_resolution)
    set_input(wf, "remesh", "resolution", q.remesh_resolution)
    if p.target_faces > 0:
        set_input(wf, "decimate", "target_face_count", p.target_faces)
    else:  # sin reducir: la malla del remesh pasa directo
        del wf["decimate"]
        wf["smooth_low"]["inputs"]["mesh"] = ["remesh", 0]
    set_input(wf, "unwrap", "resolution", q.texture_size)
    set_input(wf, "bake_color", "texture_size", q.texture_size)
    set_input(wf, "bake_normal", "resolution", q.normal_size)
    set_input(wf, "bake_ao", "resolution", q.ao_size)


BUILDERS = {
    "pixal3d": ("image_to_mesh", build_trellis_family),
    "trellis2": ("image_to_mesh", build_trellis_family),
    MULTIVIEW_FAMILY: ("image_to_mesh", build_multiview),
}
"""familia -> (plantilla, constructor)."""


def build_workflow(workflows_dir: Path, registry: Registry, params: GenerationParams) -> Workflow:
    family = registry.model(params.model_id).family
    if params.views and family != MULTIVIEW_FAMILY:
        raise WorkflowError("varias vistas requieren el modelo multivista (Pixal3D multivista)")
    if family not in BUILDERS:
        raise WorkflowError(f"familia sin adaptador todavía: {family}")
    template_name, builder = BUILDERS[family]
    return builder(load_template(workflows_dir, family, template_name), registry, params)


def output_files(outputs: dict[str, Any], node: str = "save") -> list[dict[str, Any]]:
    """Archivos 3D que produjo el nodo `node` (formato de /history)."""
    files: list[dict[str, Any]] = outputs.get(node, {}).get("3d", [])
    return files
