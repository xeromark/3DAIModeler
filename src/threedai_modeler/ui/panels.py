"""Paneles en la barra lateral del Viewport 3D (tecla N), pestaña '3DAI'."""

from __future__ import annotations

import bpy

from ..core.engine import EngineState
from ..core.pipeline import PARTS_MODEL
from ..core.registry import ModelState
from ..core.tasks import Task, TaskStatus
from ..core.workflows import BUILDERS
from . import runtime

STATE_UI = {
    EngineState.UNAVAILABLE: ("ERROR", "No disponible"),
    EngineState.NOT_INSTALLED: ("IMPORT", "No instalado"),
    EngineState.STOPPED: ("PAUSE", "Detenido"),
    EngineState.STARTING: ("SORTTIME", "Arrancando"),
    EngineState.READY: ("CHECKMARK", "Listo"),
}


def draw_task(layout: bpy.types.UILayout, task: Task | None) -> None:
    """Barra de progreso y botón cancelar de una tarea en curso, o su último resultado."""
    if task is None:
        return
    if task.running:
        row = layout.row(align=True)
        text = f"{task.message or task.title}  ({task.elapsed:.0f} s)"
        if task.fraction is not None:
            row.progress(factor=task.fraction, type="BAR", text=text)
        else:
            row.label(text=text, icon="SORTTIME")
        row.operator("threedai.cancel", text="", icon="X").kind = task.kind
    elif task.status is TaskStatus.DONE and task.message:
        layout.label(text=task.message, icon="CHECKMARK")


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "3DAI"


class THREEDAI_PT_main(_Base, bpy.types.Panel):
    bl_label = "3DAI Modeler"

    def draw(self, _context: bpy.types.Context) -> None:
        rt = runtime.get()
        if rt.last_error:
            box = self.layout.box()
            row = box.row()
            row.alert = True
            row.label(text="Error", icon="ERROR")
            row.operator("threedai.dismiss_error", text="", icon="X")
            for chunk in _wrap(rt.last_error, 60)[:6]:
                box.label(text=chunk)
            box.operator("threedai.engine_logs", icon="TEXT")


class THREEDAI_PT_engine(_Base, bpy.types.Panel):
    bl_label = "Motor"
    bl_parent_id = "THREEDAI_PT_main"

    def draw(self, _context: bpy.types.Context) -> None:
        rt = runtime.get()
        layout = self.layout
        icon, label = STATE_UI[rt.engine_state]
        layout.label(text=f"{label}: {rt.engine_message}", icon=icon)

        task = rt.tasks.get("engine")
        busy = task is not None and task.running
        row = layout.row(align=True)
        row.enabled = not busy
        state = rt.engine_state
        if state is EngineState.NOT_INSTALLED:
            row.operator("threedai.engine_install", icon="IMPORT")
        elif state in (EngineState.STOPPED, EngineState.UNAVAILABLE):
            row.operator("threedai.engine_start", icon="PLAY")
        else:
            row.operator("threedai.engine_stop", icon="PAUSE")
        row.operator("threedai.engine_logs", text="", icon="TEXT")
        draw_task(layout, task)


class THREEDAI_PT_generate(_Base, bpy.types.Panel):
    bl_label = "Generar"
    bl_parent_id = "THREEDAI_PT_main"

    def draw(self, context: bpy.types.Context) -> None:
        rt = runtime.get()
        props = context.scene.threedai
        layout = self.layout

        layout.prop(props, "image")
        layout.prop(props, "model_id")
        model_state = rt.model_states.get(props.model_id, ModelState.MISSING)
        download = rt.tasks.get("download")
        if model_state is not ModelState.INSTALLED and props.model_id:
            box = layout.box()
            missing = rt.registry.missing_bytes(rt.ws, props.model_id) / 1e9
            box.label(text=f"Modelo no descargado ({missing:.1f} GB)", icon="INFO")
            if download is None or not download.running:
                op = box.operator("threedai.model_download", icon="IMPORT")
                op.model_id = props.model_id
        draw_task(layout, download if download and download.running else None)

        col = layout.column(align=True)
        col.prop(props, "quality")
        col.prop(props, "faces")
        if props.faces == "custom":
            col.prop(props, "faces_custom")
        row = layout.row(align=True)
        row.prop(props, "random_seed", text="Aleatoria")
        sub = row.row()
        sub.enabled = not props.random_seed
        sub.prop(props, "seed")
        layout.prop(props, "object_name")
        layout.prop(props, "split_parts")
        parts_ready = True
        if props.split_parts:
            box = layout.box()
            if rt.model_states.get(PARTS_MODEL) is not ModelState.INSTALLED:
                parts_ready = False
                box.label(text="Falta el modelo de partes (0.5 GB)", icon="INFO")
                if download is None or not download.running:
                    op = box.operator("threedai.model_download", icon="IMPORT")
                    op.model_id = PARTS_MODEL
            if not rt.parts_installed:
                parts_ready = False
                box.label(text="Falta instalar el soporte de partes", icon="INFO")
                engine_task = rt.tasks.get("engine")
                if engine_task is None or not engine_task.running:
                    box.operator("threedai.parts_install", icon="IMPORT")
            if parts_ready:
                note = "Licencia Hunyuan 3D-Part: no válida en UE, UK ni Corea"
                box.label(text=note, icon="INFO")

        gen = rt.tasks.get("generate")
        busy = gen is not None and gen.running
        row = layout.row()
        row.scale_y = 1.5
        row.enabled = not busy and model_state is ModelState.INSTALLED and parts_ready
        row.operator("threedai.generate", icon="MESH_MONKEY")
        draw_task(layout, gen)


STATE_ICON = {
    ModelState.INSTALLED: "CHECKMARK",
    ModelState.PARTIAL: "DOWNARROW_HLT",
    ModelState.MISSING: "BLANK1",
}


class THREEDAI_PT_models(_Base, bpy.types.Panel):
    bl_label = "Modelos"
    bl_parent_id = "THREEDAI_PT_main"
    bl_options = {"DEFAULT_CLOSED"}  # noqa: RUF012 (atributo de clase que lee Blender)

    def draw(self, _context: bpy.types.Context) -> None:
        rt = runtime.get()
        layout = self.layout
        engine = rt.engine_disk_bytes
        disk = f"Modelos {rt.models_disk_bytes / 1e9:.1f} GB"
        if engine is not None:
            disk += f" · motor total {engine / 1e9:.1f} GB"
        layout.label(text=disk, icon="DISK_DRIVE")

        download = rt.tasks.get("download")
        busy = download is not None and download.running
        gen = rt.tasks.get("generate")
        generating = gen is not None and gen.running
        for model in rt.registry.models.values():
            state = rt.model_states.get(model.id, ModelState.MISSING)
            box = layout.box()
            row = box.row(align=True)
            row.label(text=model.name, icon=STATE_ICON[state])
            size = rt.registry.model_size(model.id) / 1e9
            sub = box.row()
            sub.scale_y = 0.7
            supported = "" if model.family in (*BUILDERS, "p3sam") else " · próximamente"
            info = f"{model.family} · {model.precision} · {size:.1f} GB · {model.license}"
            sub.label(text=info + supported)
            actions = row.row(align=True)
            actions.enabled = not busy
            if state is not ModelState.INSTALLED:
                op = actions.operator("threedai.model_download", text="", icon="IMPORT")
                op.model_id = model.id
            if state is not ModelState.MISSING:
                rm = actions.row(align=True)
                rm.enabled = not generating
                rm.operator("threedai.model_remove", text="", icon="TRASH").model_id = model.id
            if model.user_added:
                actions.operator("threedai.variant_remove", text="", icon="X").model_id = model.id
        draw_task(layout, download)
        row = layout.row()
        row.enabled = not busy
        row.operator("threedai.variant_add", icon="ADD")


class THREEDAI_PT_results(_Base, bpy.types.Panel):
    bl_label = "Resultados"
    bl_parent_id = "THREEDAI_PT_main"
    bl_options = {"DEFAULT_CLOSED"}  # noqa: RUF012 (atributo de clase que lee Blender)

    def draw(self, _context: bpy.types.Context) -> None:
        rt = runtime.get()
        if not rt.recent_jobs:
            self.layout.label(text="Todavía no hay trabajos")
            return
        gen = rt.tasks.get("generate")
        busy = gen is not None and gen.running
        for job in rt.recent_jobs:
            row = self.layout.row(align=True)
            info = f"{job.name} · {job.quality} · {job.target_faces // 1000}k"
            if job.is_done:
                row.label(text=info, icon="MESH_DATA")
                row.operator("threedai.job_import", text="", icon="IMPORT").job_id = job.id
            else:
                row.label(text=info, icon="ERROR")
                sub = row.row()
                sub.enabled = not busy
                sub.operator("threedai.job_resume", text="", icon="FILE_REFRESH").job_id = job.id


def _wrap(text: str, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width and line:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return [*lines, line] if line else lines


CLASSES = (
    THREEDAI_PT_main,
    THREEDAI_PT_engine,
    THREEDAI_PT_generate,
    THREEDAI_PT_models,
    THREEDAI_PT_results,
)
