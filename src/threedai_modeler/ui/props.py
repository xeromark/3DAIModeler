"""Propiedades de la escena: lo que el usuario elige en el panel Generar."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty

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


class THREEDAI_Props(bpy.types.PropertyGroup):
    image: StringProperty(name="Imagen", subtype="FILE_PATH")
    quality: EnumProperty(name="Calidad", items=QUALITY_ITEMS, default="standard")
    faces: EnumProperty(name="Polígonos", items=FACES_ITEMS, default="medium")
    faces_custom: IntProperty(name="Caras", default=30_000, min=100, max=5_000_000, step=1000)
    random_seed: BoolProperty(name="Semilla aleatoria", default=True)
    seed: IntProperty(name="Semilla", default=42, min=0)
    split_parts: BoolProperty(
        name="Separar en partes",
        description="Divide el modelo en partes (P3-SAM); cada parte es un objeto",
        default=False,
    )
    upright: BoolProperty(
        name="Enderezar",
        description="Gira el modelo si sale inclinado respecto al suelo (fotos desde arriba)",
        default=True,
    )
    object_name: StringProperty(
        name="Nombre", description="Nombre del objeto (vacío: el nombre de la imagen)"
    )
    show_unsupported: BoolProperty(
        name="Mostrar modelos sin soporte",
        description="Incluye modelos del catálogo que la app aún no puede usar",
        default=False,
    )

    def target_faces(self) -> int:
        presets = runtime.get().presets
        return presets.resolve_faces(self.faces_custom if self.faces == "custom" else self.faces)


CLASSES = (THREEDAI_Props,)


def register() -> None:
    bpy.types.Scene.threedai = PointerProperty(type=THREEDAI_Props)


def unregister() -> None:
    del bpy.types.Scene.threedai
