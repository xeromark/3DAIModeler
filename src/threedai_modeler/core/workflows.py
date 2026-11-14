"""Adaptadores: convierten parámetros de generación en un workflow de ComfyUI.

Cada familia de modelos tiene una plantilla en workflows/<familia>/ con IDs de nodo
legibles; los parámetros se inyectan por (nodo, entrada), nunca por posición.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
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
    seed: int


# Etiquetas para mostrar el progreso: prefijo del ID de nodo -> etapa visible.
STAGE_LABELS: tuple[tuple[str, str], ...] = (
    ("load_image", "Preparando imagen"),
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


def build_pixal3d(template: Workflow, registry: Registry, p: GenerationParams) -> Workflow:
    wf = copy.deepcopy(template)
    q = p.quality
    set_input(wf, "load_image", "image", p.image)
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
    set_input(wf, "decimate", "target_face_count", p.target_faces)
    set_input(wf, "unwrap", "resolution", q.texture_size)
    set_input(wf, "bake_color", "texture_size", q.texture_size)
    set_input(wf, "bake_normal", "resolution", q.normal_size)
    set_input(wf, "bake_ao", "resolution", q.ao_size)
    return wf


BUILDERS = {"pixal3d": ("image_to_mesh", build_pixal3d)}
"""familia -> (plantilla, constructor)."""


def build_workflow(workflows_dir: Path, registry: Registry, params: GenerationParams) -> Workflow:
    family = registry.model(params.model_id).family
    if family not in BUILDERS:
        raise WorkflowError(f"familia sin adaptador todavía: {family}")
    template_name, builder = BUILDERS[family]
    return builder(load_template(workflows_dir, family, template_name), registry, params)


def output_files(outputs: dict[str, Any], node: str = "save") -> list[dict[str, Any]]:
    """Archivos 3D que produjo el nodo `node` (formato de /history)."""
    files: list[dict[str, Any]] = outputs.get(node, {}).get("3d", [])
    return files
