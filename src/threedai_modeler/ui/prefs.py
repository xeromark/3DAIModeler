"""Preferencias del addon (Editar > Preferencias > Complementos > 3DAI Modeler)."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, StringProperty

ADDON_ID = __package__.rpartition(".")[0]
"""Nombre del módulo del addon (p. ej. bl_ext.user_default.threedai_modeler)."""


class THREEDAI_Preferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    workspace: StringProperty(
        name="Workspace",
        description="Carpeta del proyecto 3DAIModeler (vacío: detectarla automáticamente)",
        subtype="DIR_PATH",
    )
    auto_start_engine: BoolProperty(
        name="Iniciar el motor al generar",
        description="Si el motor está detenido, arrancarlo automáticamente al pulsar Generar",
        default=True,
    )
    generation_model: StringProperty(
        name="Modelo de generación",
        description="ID del modelo que se usa para generar (vacío: el predeterminado)",
    )
    stop_engine_on_exit: BoolProperty(
        name="Detener el motor al cerrar Blender",
        description="Libera la RAM y la VRAM que usa el motor",
        default=True,
    )

    def draw(self, _context: bpy.types.Context) -> None:
        col = self.layout.column()
        col.prop(self, "workspace")
        col.prop(self, "auto_start_engine")
        col.prop(self, "stop_engine_on_exit")
        col.label(text="Cambiar el workspace requiere reiniciar Blender.", icon="INFO")


def get(context: bpy.types.Context | None = None) -> THREEDAI_Preferences | None:
    ctx = context or bpy.context
    addon = ctx.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None  # type: ignore[return-value]


CLASSES = (THREEDAI_Preferences,)


def generation_model_id(context: bpy.types.Context | None = None) -> str:
    """Modelo de generación elegido, o el predeterminado del catálogo."""
    from . import runtime

    preferences = get(context)
    chosen = preferences.generation_model if preferences else ""
    registry = runtime.get().registry
    if chosen in registry.models:
        return chosen
    default = registry.default_model()
    return default.id if default else ""


def effective_model_id(context: bpy.types.Context | None = None) -> str:
    """Modelo que se usará al generar: el multivista si hay vistas adicionales."""
    from ..core.workflows import MULTIVIEW_FAMILY
    from . import runtime

    ctx = context or bpy.context
    if ctx.scene.threedai.extra_views():
        registry = runtime.get().registry
        candidates = registry.models_of_family(MULTIVIEW_FAMILY)
        return candidates[0].id if candidates else ""
    return generation_model_id(ctx)
