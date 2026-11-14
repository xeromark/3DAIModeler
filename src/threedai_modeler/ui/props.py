"""Propiedades de la escena: lo que el usuario elige en el panel Generar."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty

from ..core.registry import ModelState
from ..core.workflows import BUILDERS
from . import runtime

QUALITY_ITEMS = [
    ("draft", "Borrador", "Rápido, para probar la imagen"),
    ("standard", "Estándar", "Valores de la plantilla oficial"),
    ("max", "Máxima", "Máxima calidad, sin importar el tiempo"),
]

FACES_ITEMS = [
    ("low", "Baja (10k)", "10.000 caras"),
    ("medium", "Media (50k)", "50.000 caras"),
    ("high", "Alta (200k)", "200.000 caras"),
    ("ultra", "Ultra (700k)", "700.000 caras"),
    ("custom", "Personalizada", "Número exacto de caras"),
]

_model_items: list[tuple[str, str, str]] = []
"""Blender exige mantener viva la lista de un EnumProperty dinámico."""


def model_items(_self: object, _context: object) -> list[tuple[str, str, str]]:
    global _model_items
    try:
        rt = runtime.get()
        _model_items = []
        for m in rt.registry.models.values():
            if m.family not in BUILDERS:  # familias sin adaptador todavía
                continue
            installed = rt.model_states.get(m.id) is ModelState.INSTALLED
            label = m.name if installed else f"{m.name} (sin descargar)"
            _model_items.append((m.id, label, f"{m.family} · {m.precision} · {m.license}"))
    except RuntimeError:
        _model_items = []
    return _model_items or [("", "Sin modelos", "")]


class THREEDAI_Props(bpy.types.PropertyGroup):
    image: StringProperty(name="Imagen", subtype="FILE_PATH")
    model_id: EnumProperty(name="Modelo", items=model_items)
    quality: EnumProperty(name="Calidad", items=QUALITY_ITEMS, default="standard")
    faces: EnumProperty(name="Polígonos", items=FACES_ITEMS, default="medium")
    faces_custom: IntProperty(name="Caras", default=30_000, min=100, max=5_000_000, step=1000)
    random_seed: BoolProperty(name="Semilla aleatoria", default=True)
    seed: IntProperty(name="Semilla", default=42, min=0)
    object_name: StringProperty(
        name="Nombre", description="Nombre del objeto (vacío: el nombre de la imagen)"
    )

    def target_faces(self) -> int:
        presets = runtime.get().presets
        return presets.resolve_faces(self.faces_custom if self.faces == "custom" else self.faces)


CLASSES = (THREEDAI_Props,)


def register() -> None:
    bpy.types.Scene.threedai = PointerProperty(type=THREEDAI_Props)


def unregister() -> None:
    del bpy.types.Scene.threedai
