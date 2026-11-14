"""Propiedades de la escena: lo que el usuario elige en el panel Generar."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from . import runtime
from .styles import STYLE_ITEMS

QUALITY_ITEMS = [
    ("draft", "Borrador", "Rápido, para probar la imagen"),
    ("standard", "Estándar", "Valores de la plantilla oficial"),
    ("max", "Máxima", "Todo al máximo (texturas 8K), sin importar el tiempo"),
]

FACES_ITEMS = [
    ("low", "Baja (10k)", "10.000 caras"),
    ("medium", "Media (50k)", "50.000 caras"),
    ("high", "Alta (200k)", "200.000 caras"),
    ("ultra", "Ultra (700k)", "700.000 caras"),
    ("none", "Sin reducir", "Malla completa (1 a 2 millones de caras): impresión 3D o esculpir"),
    ("custom", "Personalizada", "Número exacto de caras"),
]


def _apply_style(_self: object, context: bpy.types.Context) -> None:
    from . import styles

    styles.apply_style(context)


class THREEDAI_Props(bpy.types.PropertyGroup):
    image: StringProperty(name="Imagen", subtype="FILE_PATH")
    view_left: StringProperty(name="Izquierda", subtype="FILE_PATH")
    view_back: StringProperty(name="Espalda", subtype="FILE_PATH")
    view_right: StringProperty(name="Derecha", subtype="FILE_PATH")
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
    style: EnumProperty(name="Estilo", items=STYLE_ITEMS, default="NONE", update=_apply_style)
    style_intensity: FloatProperty(
        name="Intensidad", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=_apply_style
    )
    style_apply_to: EnumProperty(
        name="Aplicar a",
        items=[
            ("GENERATED", "Modelos generados", "Todos los objetos importados por el addon"),
            ("SELECTED", "Selección", "Los objetos seleccionados"),
        ],
        default="GENERATED",
        update=_apply_style,
    )
    cel_bands: IntProperty(
        name="Bandas",
        description="Niveles de sombra",
        default=3,
        min=2,
        max=4,
        update=_apply_style,
    )
    cel_outline: BoolProperty(name="Contorno", default=True, update=_apply_style)
    outline_thickness: FloatProperty(
        name="Grosor",
        description="Grosor del contorno, relativo al tamaño del objeto",
        default=0.006,
        min=0.001,
        max=0.03,
        precision=3,
        update=_apply_style,
    )
    show_unsupported: BoolProperty(
        name="Mostrar modelos sin soporte",
        description="Incluye modelos del catálogo que la app aún no puede usar",
        default=False,
    )

    def extra_views(self) -> dict[str, Path]:
        """Vistas adicionales cargadas: left/back/right -> ruta."""
        views = {}
        for view in ("left", "back", "right"):
            value = getattr(self, f"view_{view}")
            if value:
                views[view] = Path(bpy.path.abspath(value))
        return views

    def target_faces(self) -> int:
        presets = runtime.get().presets
        return presets.resolve_faces(self.faces_custom if self.faces == "custom" else self.faces)


CLASSES = (THREEDAI_Props,)


def register() -> None:
    bpy.types.Scene.threedai = PointerProperty(type=THREEDAI_Props)


def unregister() -> None:
    del bpy.types.Scene.threedai
