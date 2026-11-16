"""Paneles en la barra lateral del Viewport 3D (tecla N), pestaña '3DAI'.

Organización:
- Motor: estado, uso de VRAM/RAM en vivo, modelos en uso y gestión de modelos.
- Generar: solo lo que cambia en cada modelo (imagen, calidad, polígonos, opciones).
- Resultados: trabajos anteriores con miniatura y acciones.
"""

from __future__ import annotations

from pathlib import Path

import bpy

from ..core.engine import EngineState
from ..core.jobs import estimate
from ..core.pipeline import PARTS_MODEL
from ..core.registry import ModelState
from ..core.tasks import Task, TaskStatus
from ..core.workflows import BUILDERS
from . import prefs, previews, runtime

GiB = 1024**3
SUPPORTED_FAMILIES = (*BUILDERS, "p3sam")

STATE_UI = {
    EngineState.UNAVAILABLE: ("ERROR", "No disponible"),
    EngineState.NOT_INSTALLED: ("IMPORT", "No instalado"),
    EngineState.STOPPED: ("PAUSE", "Detenido"),
    EngineState.STARTING: ("SORTTIME", "Arrancando…"),
    EngineState.READY: ("CHECKMARK", "Listo"),
}


# --------------------------------------------------------------- utilidades


def wrapped(
    layout: bpy.types.UILayout, context: bpy.types.Context, text: str, icon: str = "NONE"
) -> None:
    """Texto en varias líneas según el ancho del panel (en lugar de cortarlo con '…')."""
    width = context.region.width if context.region else 300
    chars = max(18, int(width / (7.0 * context.preferences.view.ui_scale)))
    col = layout.column(align=True)
    col.scale_y = 0.85
    lines: list[str] = []
    line = ""
    for word in text.split():
        if line and len(line) + len(word) + 1 > chars:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    for i, text_line in enumerate(lines):
        line_icon = icon if i == 0 else ("BLANK1" if icon != "NONE" else "NONE")
        col.label(text=text_line, icon=line_icon)


def gib(n: int | None) -> str:
    return "—" if n is None else f"{n / GiB:.1f}"


def draw_task(layout: bpy.types.UILayout, task: Task | None, context: bpy.types.Context) -> None:
    """Barra de progreso y cancelar si la tarea corre; si terminó, su mensaje final."""
    if task is None:
        return
    if task.running:
        row = layout.row(align=True)
        text = f"{task.message or task.title} · {task.elapsed:.0f} s"
        if task.fraction is not None:
            row.progress(factor=task.fraction, type="BAR", text=text)
        else:
            row.label(text=text, icon="SORTTIME")
        row.operator("threedai.cancel", text="", icon="X").kind = task.kind
    elif task.status is TaskStatus.DONE and task.message:
        wrapped(layout, context, task.message, icon="CHECKMARK")


def usage_bar(layout: bpy.types.UILayout, label: str, used: int | None, total: int | None) -> None:
    if used is None or not total:
        layout.label(text=f"{label}: —")
        return
    text = f"{label}  {gib(used)} / {gib(total)} GB"
    layout.progress(factor=min(1.0, used / total), type="BAR", text=text)


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "3DAI"


# ------------------------------------------------------------------- paneles


class THREEDAI_PT_main(_Base, bpy.types.Panel):
    bl_label = "3DAI Modeler"

    def draw(self, context: bpy.types.Context) -> None:
        rt = runtime.get()
        if not rt.last_error:
            return
        box = self.layout.box()
        row = box.row()
        row.alert = True
        row.label(text="Error", icon="ERROR")
        row.operator("threedai.dismiss_error", text="", icon="X")
        wrapped(box, context, rt.last_error)
        box.operator("threedai.engine_logs", icon="TEXT")


class THREEDAI_PT_engine(_Base, bpy.types.Panel):
    bl_label = "Motor"
    bl_parent_id = "THREEDAI_PT_main"

    def draw(self, context: bpy.types.Context) -> None:
        rt = runtime.get()
        layout = self.layout
        state = rt.engine_state
        icon, label = STATE_UI[state]

        row = layout.row(align=True)
        row.label(text=label, icon=icon)
        task = rt.tasks.get("engine")
        buttons = row.row(align=True)
        buttons.enabled = task is None or not task.running
        if state is EngineState.NOT_INSTALLED:
            buttons.operator("threedai.engine_install", text="Instalar", icon="IMPORT")
        elif state in (EngineState.STOPPED, EngineState.UNAVAILABLE):
            buttons.operator("threedai.engine_start", text="Iniciar", icon="PLAY")
        else:
            buttons.operator("threedai.engine_stop", text="Detener", icon="PAUSE")
        buttons.operator("threedai.engine_logs", text="", icon="TEXT")
        if state is not EngineState.READY:
            wrapped(layout, context, rt.engine_message)
        draw_task(layout, task, context)

        if state is EngineState.READY and rt.usage is not None:
            self._draw_usage(layout)
        self._draw_models_in_use(layout, context)
        draw_task(layout, rt.tasks.get("download"), context)

    @staticmethod
    def _draw_usage(layout: bpy.types.UILayout) -> None:
        """VRAM y RAM en vivo (toda la GPU / todo el sistema) y la parte del motor."""
        rt = runtime.get()
        usage = rt.usage
        assert usage is not None
        col = layout.column(align=True)
        usage_bar(col, "VRAM", usage.vram_used, usage.vram_total)
        usage_bar(col, "RAM", usage.ram_used, usage.ram_total)
        detail = []
        if usage.engine_vram:
            detail.append(f"VRAM {gib(usage.engine_vram)} GB")
        if rt.engine_ram is not None:
            detail.append(f"RAM {gib(rt.engine_ram)} GB")
        if detail:
            sub = col.row()
            sub.scale_y = 0.8
            sub.label(text="Motor: " + " · ".join(detail))

    @staticmethod
    def _draw_models_in_use(layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
        rt = runtime.get()
        box = layout.box()
        box.label(text="Modelos en uso", icon="OUTLINER_DATA_MESH")
        model_id = prefs.generation_model_id(context)
        model = rt.registry.models.get(model_id)
        split = box.split(factor=0.35)
        split.label(text="Generación")
        split.operator_menu_enum(
            "threedai.select_model", "model_id", text=model.name if model else "Elegir…"
        )
        if model is not None and rt.model_states.get(model_id) is not ModelState.INSTALLED:
            missing = rt.registry.missing_bytes(rt.ws, model_id) / 1e9
            op = box.operator(
                "threedai.model_download", text=f"Descargar ({missing:.1f} GB)", icon="IMPORT"
            )
            op.model_id = model_id
        split = box.split(factor=0.35)
        split.label(text="Partes")
        parts_ok = rt.model_states.get(PARTS_MODEL) is ModelState.INSTALLED and rt.parts_installed
        split.label(text="P3-SAM" if parts_ok else "P3-SAM (sin instalar)")


class THREEDAI_PT_models(_Base, bpy.types.Panel):
    bl_label = "Gestionar modelos"
    bl_parent_id = "THREEDAI_PT_engine"
    bl_options = {"DEFAULT_CLOSED"}  # noqa: RUF012 (atributo de clase que lee Blender)

    def draw(self, context: bpy.types.Context) -> None:
        rt = runtime.get()
        props = context.scene.threedai
        layout = self.layout
        disk = f"En disco: modelos {rt.models_disk_bytes / 1e9:.1f} GB"
        if rt.engine_disk_bytes is not None:
            disk += f" · motor {rt.engine_disk_bytes / 1e9:.1f} GB"
        layout.label(text=disk, icon="DISK_DRIVE")

        download = rt.tasks.get("download")
        busy = download is not None and download.running
        models = [
            m
            for m in rt.registry.models.values()
            if props.show_unsupported or m.family in SUPPORTED_FAMILIES
        ]
        installed = [m for m in models if rt.model_states.get(m.id) is ModelState.INSTALLED]
        available = [m for m in models if m not in installed]
        for title, group in (("Instalados", installed), ("Disponibles", available)):
            if group:
                layout.label(text=title)
                for model in group:
                    self._draw_model(layout, model.id, busy)

        layout.prop(props, "show_unsupported")
        col = layout.column(align=True)
        col.enabled = not busy
        col.operator("threedai.variant_add", text="Desde Hugging Face…", icon="URL")
        col.operator("threedai.model_import_file", text="Desde archivo…", icon="FILE_FOLDER")
        draw_task(layout, download, context)

    @staticmethod
    def _draw_model(layout: bpy.types.UILayout, model_id: str, busy: bool) -> None:
        rt = runtime.get()
        model = rt.registry.model(model_id)
        state = rt.model_states.get(model_id, ModelState.MISSING)
        gen = rt.tasks.get("generate")
        generating = gen is not None and gen.running

        box = layout.box()
        row = box.row(align=True)
        row.label(text=model.name, icon="CHECKMARK" if state is ModelState.INSTALLED else "DOT")
        actions = row.row(align=True)
        actions.enabled = not busy
        if state is not ModelState.INSTALLED:
            actions.operator("threedai.model_download", text="", icon="IMPORT").model_id = model_id
        if state is not ModelState.MISSING:
            remove = actions.row(align=True)
            remove.enabled = not generating
            remove.operator("threedai.model_remove", text="", icon="TRASH").model_id = model_id
        if model.user_added:
            actions.operator("threedai.variant_remove", text="", icon="X").model_id = model_id

        size = rt.registry.model_size(model_id) / 1e9
        details = [model.precision, f"{size:.1f} GB", model.license]
        if any(rt.registry.files[f].is_local for f in model.files):
            details.insert(0, "archivo local")
        elif model.user_added:
            details.insert(0, "variante")
        if model.family not in SUPPORTED_FAMILIES:
            details.append("sin soporte aún")
        info = box.row()
        info.scale_y = 0.75
        info.label(text=" · ".join(details))


class THREEDAI_PT_generate(_Base, bpy.types.Panel):
    bl_label = "Generar"
    bl_parent_id = "THREEDAI_PT_main"

    def draw(self, context: bpy.types.Context) -> None:
        rt = runtime.get()
        props = context.scene.threedai
        layout = self.layout

        views = props.extra_views()
        layout.prop(props, "image", text="Frente" if views else "")
        image = Path(bpy.path.abspath(props.image)) if props.image else None
        if image is not None and image.is_file():
            layout.template_icon(icon_value=previews.icon(image), scale=6.0)

        col = layout.column(align=True)
        col.prop(props, "quality")
        col.prop(props, "faces")
        if props.faces == "custom":
            col.prop(props, "faces_custom")

        model_id = prefs.effective_model_id(context)
        est = estimate(rt.all_jobs, model_id, props.quality, props.split_parts)
        if est is not None:
            sub = layout.row()
            sub.scale_y = 0.8
            minutes = est.seconds / 60
            text = f"≈ {minutes:.1f} min · hasta {est.peak_vram_gib:.1f} GB VRAM"
            sub.label(text=text, icon="TIME")

        layout.prop(props, "object_name")
        row = layout.row()
        row.prop(props, "split_parts")
        row.prop(props, "upright")
        layout.prop(props, "project_texture")

        ready = rt.model_states.get(model_id) is ModelState.INSTALLED
        if not ready and views:
            box = layout.box()
            wrapped(box, context, "Varias vistas usan el modelo Pixal3D multivista.", icon="INFO")
            if model_id:
                missing = rt.registry.missing_bytes(rt.ws, model_id) / 1e9
                op = box.operator(
                    "threedai.model_download", text=f"Descargar ({missing:.1f} GB)", icon="IMPORT"
                )
                op.model_id = model_id
        elif not ready:
            wrapped(layout, context, "Descarga el modelo de generación en Motor.", icon="INFO")
        if props.split_parts and not self._parts_ready(layout, context):
            ready = False

        gen = rt.tasks.get("generate")
        row = layout.row()
        row.scale_y = 1.6
        row.enabled = ready and (gen is None or not gen.running)
        row.operator("threedai.generate", icon="MESH_MONKEY")
        draw_task(layout, gen, context)

    @staticmethod
    def _parts_ready(layout: bpy.types.UILayout, context: bpy.types.Context) -> bool:
        rt = runtime.get()
        box = layout.box()
        ok = True
        if rt.model_states.get(PARTS_MODEL) is not ModelState.INSTALLED:
            ok = False
            op = box.operator(
                "threedai.model_download", text="Descargar P3-SAM (0.5 GB)", icon="IMPORT"
            )
            op.model_id = PARTS_MODEL
        if not rt.parts_installed:
            ok = False
            box.operator("threedai.parts_install", icon="IMPORT")
        if ok:
            wrapped(box, context, "Licencia Hunyuan 3D-Part: no válida en UE, UK ni Corea.")
        return ok


class THREEDAI_PT_generate_views(_Base, bpy.types.Panel):
    bl_label = "Vistas adicionales"
    bl_parent_id = "THREEDAI_PT_generate"
    bl_options = {"DEFAULT_CLOSED"}  # noqa: RUF012 (atributo de clase que lee Blender)

    def draw_header(self, context: bpy.types.Context) -> None:
        count = len(context.scene.threedai.extra_views())
        if count:
            self.layout.label(text=f"({count})")

    def draw(self, context: bpy.types.Context) -> None:
        props = context.scene.threedai
        layout = self.layout
        wrapped(
            layout,
            context,
            "Opcional. Misma pose y escala que el frente; mejora la espalda y los costados.",
        )
        row = layout.row()
        for view in ("left", "back", "right"):
            col = row.column(align=True)
            value = getattr(props, f"view_{view}")
            path = Path(bpy.path.abspath(value)) if value else None
            if path is not None and path.is_file():
                col.template_icon(icon_value=previews.icon(path), scale=3.0)
            col.prop(props, f"view_{view}", text="")
            col.label(text={"left": "Izquierda", "back": "Espalda", "right": "Derecha"}[view])


class THREEDAI_PT_generate_advanced(_Base, bpy.types.Panel):
    """Solo con calidad Personalizada: todos los parámetros de calidad y exportación."""

    bl_label = "Avanzado"
    bl_parent_id = "THREEDAI_PT_generate"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return bool(context.scene.threedai.quality == "custom")

    def draw(self, context: bpy.types.Context) -> None:
        props = context.scene.threedai
        layout = self.layout

        row = layout.row(align=True)
        row.label(text="Partir de:")
        for preset, label in (("draft", "Borrador"), ("standard", "Estándar"), ("max", "Máxima")):
            row.operator("threedai.quality_from_preset", text=label).preset = preset

        col = layout.column(align=True)
        col.label(text="Pasos de la IA (más = mejor y más lento)")
        grid = col.grid_flow(columns=2, align=True)
        for key in ("structure_steps", "shape_steps", "upsample_steps", "texture_steps"):
            grid.prop(props, f"q_{key}")

        col = layout.column(align=True)
        col.label(text="Resolución")
        col.prop(props, "q_upsample_resolution")
        col.prop(props, "q_remesh_resolution")

        col = layout.column(align=True)
        col.label(text="Texturas")
        col.prop(props, "q_texture_size")
        col.prop(props, "q_normal_size")
        col.prop(props, "q_ao_size")
        usage = runtime.get().usage
        vram_gb = usage.vram_total / GiB if usage and usage.vram_total else 16.0
        if int(props.q_normal_size) > 4096 and vram_gb < 20:
            text = (
                f"El normal map 8K necesita ~12 GB de VRAM solo para hornearse: no cabe en "
                f"tu GPU de {vram_gb:.0f} GB. Usa 4K."
            )
            wrapped(layout, context, text, icon="ERROR")

        row = layout.row(align=True)
        row.prop(props, "random_seed", text="Semilla aleatoria")
        sub = row.row()
        sub.enabled = not props.random_seed
        sub.prop(props, "seed", text="")

        row = layout.row(align=True)
        row.label(text="Exportar también:")
        row.prop(props, "export_fbx", toggle=True)
        row.prop(props, "export_obj", toggle=True)


class THREEDAI_PT_styles(_Base, bpy.types.Panel):
    bl_label = "Estilos"
    bl_parent_id = "THREEDAI_PT_main"
    bl_options = {"DEFAULT_CLOSED"}  # noqa: RUF012 (atributo de clase que lee Blender)

    def draw(self, context: bpy.types.Context) -> None:
        props = context.scene.threedai
        layout = self.layout
        layout.prop(props, "style", text="")
        if props.style == "NONE":
            wrapped(layout, context, "Elige un estilo para cambiar cómo se ve la escena.")
            return
        col = layout.column(align=True)
        col.prop(props, "style_intensity", slider=True)
        if props.style == "ANIME":
            col.prop(props, "cel_bands")
            row = col.row(align=True)
            row.prop(props, "cel_outline")
            sub = row.row(align=True)
            sub.enabled = props.cel_outline
            sub.prop(props, "outline_thickness", text="")
            layout.prop(props, "style_apply_to")
        layout.operator("threedai.style_reset", icon="LOOP_BACK")


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
            box = self.layout.box()
            row = box.row()
            thumb = previews.icon(job.input_path)
            if thumb:
                row.template_icon(icon_value=thumb, scale=2.2)
            col = row.column(align=True)
            col.label(text=job.name)
            faces = f"{job.target_faces // 1000}k caras" if job.target_faces else "sin reducir"
            info = f"{job.quality} · {faces}"
            if job.parts:
                info += " · partes"
            if job.views:
                info += f" · {len(job.views) + 1} vistas"
            sub = col.row()
            sub.scale_y = 0.75
            sub.label(text=info if job.is_done else f"{info} · incompleto")
            actions = col.row(align=True)
            if job.is_done:
                op = actions.operator("threedai.job_import", text="Importar", icon="IMPORT")
                op.job_id = job.id
            else:
                resume = actions.row(align=True)
                resume.enabled = not busy
                op = resume.operator("threedai.job_resume", text="Reanudar", icon="FILE_REFRESH")
                op.job_id = job.id
            folder = actions.operator("threedai.job_open_folder", text="", icon="FILE_FOLDER")
            folder.job_id = job.id
            actions.operator("threedai.job_delete", text="", icon="TRASH").job_id = job.id


CLASSES = (
    THREEDAI_PT_main,
    THREEDAI_PT_engine,
    THREEDAI_PT_models,
    THREEDAI_PT_generate,
    THREEDAI_PT_generate_views,
    THREEDAI_PT_generate_advanced,
    THREEDAI_PT_styles,
    THREEDAI_PT_results,
)
