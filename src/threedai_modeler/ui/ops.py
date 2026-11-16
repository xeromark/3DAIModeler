"""Operadores. Cada acción lenta corre como tarea de fondo; aquí solo se lanza."""

from __future__ import annotations

import random
import shutil
from pathlib import Path
from typing import Any

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from ..core.engine import EngineState
from ..core.jobs import Job
from ..core.local_models import ImportMode, import_local
from ..core.pipeline import PARTS_MODEL, Pipeline, Progress
from ..core.presets import PresetError, custom_quality, quality_values
from ..core.registry import APPROVED_ORGS, ModelState, fetch_hf_file, list_hf_safetensors
from ..core.tasks import Task, TaskBusyError, TaskContext, TaskStatus
from ..core.workflows import BUILDERS
from . import prefs, runtime, styles


def _start(op: bpy.types.Operator, kind: str, title: str, fn: Any, on_done: Any = None) -> set[str]:
    rt = runtime.get()
    rt.last_error = None
    try:
        rt.tasks.start(kind, title, fn, on_done)
    except TaskBusyError as err:
        op.report({"WARNING"}, str(err))
        return {"CANCELLED"}
    rt.version += 1
    return {"FINISHED"}


def _refresh_after(task: Task) -> None:
    rt = runtime.get()
    if task.status is TaskStatus.FAILED:
        rt.last_error = f"{task.title}: {task.error}"
    rt.refresh()


# ------------------------------------------------------------------- motor


class THREEDAI_OT_engine_install(bpy.types.Operator):
    bl_idname = "threedai.engine_install"
    bl_label = "Instalar motor"
    bl_description = "Construye la imagen de Docker e instala ComfyUI y PyTorch (~8 GB)"

    def execute(self, _context: bpy.types.Context) -> set[str]:
        manager = runtime.get().manager

        def work(ctx: TaskContext) -> None:
            def log(line: str) -> None:
                ctx.log(line)
                ctx.report(line[:80])

            manager.install(log, ctx.cancel)

        return _start(self, "engine", "Instalar motor", work, _refresh_after)


class THREEDAI_OT_engine_start(bpy.types.Operator):
    bl_idname = "threedai.engine_start"
    bl_label = "Iniciar motor"
    bl_description = "Arranca ComfyUI en Docker (solo accesible desde este PC)"

    def execute(self, _context: bpy.types.Context) -> set[str]:
        rt = runtime.get()

        def work(ctx: TaskContext) -> None:
            ctx.report("Arrancando el motor...")
            rt.manager.start(wait=True)

        return _start(self, "engine", "Iniciar motor", work, _refresh_after)


class THREEDAI_OT_engine_stop(bpy.types.Operator):
    bl_idname = "threedai.engine_stop"
    bl_label = "Detener motor"
    bl_description = "Detiene el motor y libera su RAM y VRAM"

    def execute(self, _context: bpy.types.Context) -> set[str]:
        rt = runtime.get()
        if (gen := rt.tasks.get("generate")) is not None and gen.running:
            self.report({"WARNING"}, "Hay una generación en curso; cancélala primero")
            return {"CANCELLED"}

        def work(ctx: TaskContext) -> None:
            ctx.report("Deteniendo el motor...")
            rt.manager.stop()

        return _start(self, "engine", "Detener motor", work, _refresh_after)


class THREEDAI_OT_parts_install(bpy.types.Operator):
    bl_idname = "threedai.parts_install"
    bl_label = "Instalar soporte de partes"
    bl_description = "Instala P3-SAM (Hunyuan3D-Part) en un entorno aparte dentro del motor (~3 GB)"

    def execute(self, _context: bpy.types.Context) -> set[str]:
        runner = runtime.get().manager.runner

        def work(ctx: TaskContext) -> None:
            def log(line: str) -> None:
                ctx.log(line)
                ctx.report(line[:80])

            runner.install_parts(log, ctx.cancel)

        return _start(self, "engine", "Instalar soporte de partes", work, _refresh_after)


class THREEDAI_OT_engine_logs(bpy.types.Operator):
    bl_idname = "threedai.engine_logs"
    bl_label = "Ver logs"
    bl_description = "Copia los últimos logs del motor a un texto de Blender"

    def execute(self, context: bpy.types.Context) -> set[str]:
        rt = runtime.get()
        lines = []
        for kind in ("engine", "download", "generate"):
            task = rt.tasks.get(kind)
            if task is not None and task.logs:
                lines += [f"===== {task.title} =====", *task.logs]
        try:
            lines += ["===== Motor (docker compose logs) =====", rt.manager.runner.logs(300)]
        except Exception as err:
            lines.append(f"(sin logs del motor: {err})")
        text = bpy.data.texts.get("3DAI Modeler.log") or bpy.data.texts.new("3DAI Modeler.log")
        text.clear()
        text.write("\n".join(lines))
        self.report({"INFO"}, "Logs en el editor de texto: '3DAI Modeler.log'")
        for area in context.screen.areas:
            if area.type == "TEXT_EDITOR":
                area.spaces.active.text = text
                break
        return {"FINISHED"}


# ----------------------------------------------------------------- modelos


class THREEDAI_OT_model_download(bpy.types.Operator):
    bl_idname = "threedai.model_download"
    bl_label = "Descargar modelo"
    bl_description = "Descarga el modelo verificando cada archivo (SHA-256)"

    model_id: StringProperty()

    def execute(self, _context: bpy.types.Context) -> set[str]:
        rt = runtime.get()
        model_id = self.model_id

        def work(ctx: TaskContext) -> None:
            name = rt.registry.model(model_id).name

            def progress(done: int, total: int) -> None:
                ctx.report(f"{name}: {done / 1e9:.1f} / {total / 1e9:.1f} GB", done / total)

            rt.registry.download_model(rt.ws, model_id, progress, ctx.cancel)

        def done(task: Task) -> None:
            _refresh_after(task)
            rt.reload_registry()

        return _start(self, "download", f"Descargar {model_id}", work, done)


class THREEDAI_OT_model_remove(bpy.types.Operator):
    bl_idname = "threedai.model_remove"
    bl_label = "Borrar descarga"
    bl_description = "Borra los archivos del modelo que no usa ningún otro modelo instalado"

    model_id: StringProperty()

    def invoke(self, context: bpy.types.Context, _event: bpy.types.Event) -> set[str]:
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, _context: bpy.types.Context) -> set[str]:
        rt = runtime.get()
        if (task := rt.tasks.get("generate")) is not None and task.running:
            self.report({"WARNING"}, "Hay una generación en curso")
            return {"CANCELLED"}
        removed = rt.registry.remove_model(rt.ws, self.model_id)
        freed = sum(1 for _ in removed)
        self.report({"INFO"}, f"{freed} archivo(s) borrado(s)")
        rt.reload_registry()
        return {"FINISHED"}


NO_FILE = "__none__"
_repo_cache: dict[str, tuple[list[tuple[str, str, str]], str]] = {}
"""repo -> (opciones del menú de archivos, error). Blender exige mantener viva la lista."""


def _repo_file_items(self: Any, _context: Any) -> list[tuple[str, str, str]]:
    repo = self.repo.strip()
    if "/" not in repo:
        return [(NO_FILE, "Escribe el repositorio primero", "")]
    if repo not in _repo_cache:
        try:
            files = list_hf_safetensors(repo, timeout=8)
            items = [(p, f"{p}  ({size / 1e9:.1f} GB)", p) for p, size in files]
            _repo_cache[repo] = (items or [(NO_FILE, "Sin archivos .safetensors", "")], "")
        except ValueError as err:
            _repo_cache[repo] = ([(NO_FILE, "No se pudo leer el repositorio", "")], str(err))
    return _repo_cache[repo][0]


FAMILY_ITEMS = [
    ("pixal3d", "Pixal3D", "Variante de Pixal3D (pesos de difusión)"),
    ("trellis2", "TRELLIS.2", "Variante de TRELLIS.2 (pesos de difusión)"),
]


class THREEDAI_OT_variant_add(bpy.types.Operator):
    bl_idname = "threedai.variant_add"
    bl_label = "Agregar variante de modelo"
    bl_description = (
        "Agrega pesos de Hugging Face para una familia soportada. "
        "Solo .safetensors; la revisión y el SHA-256 quedan fijados"
    )

    family: EnumProperty(name="Familia", items=FAMILY_ITEMS)
    repo: StringProperty(name="Repositorio", description="organizacion/nombre en Hugging Face")
    path: EnumProperty(name="Archivo", items=_repo_file_items)
    name: StringProperty(name="Nombre", description="Nombre visible, p. ej. 'bf16'")
    precision: StringProperty(name="Precisión", default="bf16")
    license: StringProperty(name="Licencia", default="desconocida")
    trust_org: BoolProperty(
        name="Confío en esta organización",
        description="Necesario si el repositorio no es de una organización aprobada",
    )

    def invoke(self, context: bpy.types.Context, _event: bpy.types.Event) -> set[str]:
        return context.window_manager.invoke_props_dialog(self, width=480)

    def draw(self, _context: bpy.types.Context) -> None:
        col = self.layout.column()
        col.prop(self, "repo")
        if _repo_cache.get(self.repo.strip(), ([], ""))[1]:
            col.label(text=_repo_cache[self.repo.strip()][1], icon="ERROR")
        for prop in ("path", "family", "name", "precision", "license"):
            col.prop(self, prop)
        org = self.repo.split("/", 1)[0]
        if org and org not in APPROVED_ORGS:
            box = col.box()
            box.alert = True
            box.label(text=f"'{org}' no es una organización aprobada.", icon="ERROR")
            box.prop(self, "trust_org")

    def execute(self, _context: bpy.types.Context) -> set[str]:
        rt = runtime.get()
        repo, path = self.repo.strip(), self.path
        name = self.name.strip() or Path(path).stem
        if not repo or path == NO_FILE:
            self.report({"ERROR"}, "Indica el repositorio y el archivo")
            return {"CANCELLED"}
        if repo.split("/", 1)[0] not in APPROVED_ORGS and not self.trust_org:
            self.report({"ERROR"}, "Confirma que confías en la organización")
            return {"CANCELLED"}
        family, precision, license_ = self.family, self.precision.strip(), self.license.strip()

        def work(ctx: TaskContext) -> str:
            ctx.report(f"Consultando {repo} en Hugging Face...")
            weights = fetch_hf_file(repo, path)
            model = rt.registry.make_variant(
                weights, family=family, name=name, precision=precision, license=license_
            )
            rt.registry.add_user_variant(rt.ws, weights, model)
            return model.id

        def done(task: Task) -> None:
            _refresh_after(task)
            rt.reload_registry()
            if task.status is TaskStatus.DONE:
                task.message = f"Agregado: {task.result} (descárgalo en Modelos)"

        return _start(self, "download", f"Agregar {repo}", work, done)


class THREEDAI_OT_variant_remove(bpy.types.Operator):
    bl_idname = "threedai.variant_remove"
    bl_label = "Quitar variante"
    bl_description = "Quita la variante del catálogo y borra su descarga"

    model_id: StringProperty()

    def invoke(self, context: bpy.types.Context, _event: bpy.types.Event) -> set[str]:
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, _context: bpy.types.Context) -> set[str]:
        rt = runtime.get()
        try:
            rt.registry.remove_user_variant(rt.ws, self.model_id)
        except ValueError as err:
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}
        rt.reload_registry()
        return {"FINISHED"}


# ---------------------------------------------------------------- generar


def import_job(context: bpy.types.Context, job: Job) -> list[bpy.types.Object]:
    """Añade los objetos del .blend del trabajo a una colección nueva en la escena."""
    blend = job.output_path("postprocess", "blend")
    if blend is None or not blend.is_file():
        raise FileNotFoundError(f"el trabajo {job.id} no tiene .blend")
    with bpy.data.libraries.load(str(blend), link=False) as (src, dst):
        dst.objects = list(src.objects)
    collection = bpy.data.collections.new(job.name)
    context.scene.collection.children.link(collection)
    for obj in context.selected_objects:
        obj.select_set(False)
    objects = [o for o in dst.objects if o is not None]
    for obj in objects:
        obj[styles.JOB_KEY] = job.id
        collection.objects.link(obj)
        obj.location = context.scene.cursor.location
        obj.select_set(True)
    if objects:
        context.view_layer.objects.active = objects[0]
    if context.scene.threedai.style != "NONE":
        styles.apply_style(context)  # el modelo nuevo toma el estilo activo
    return objects


class THREEDAI_OT_generate(bpy.types.Operator):
    bl_idname = "threedai.generate"
    bl_label = "Generar modelo 3D"
    bl_description = "Imagen → modelo 3D texturizado, importado en la escena"

    def execute(self, context: bpy.types.Context) -> set[str]:
        rt = runtime.get()
        props = context.scene.threedai
        model_id = prefs.effective_model_id(context)
        views = props.extra_views()
        image = Path(bpy.path.abspath(props.image))
        if not image.is_file():
            self.report({"ERROR"}, "Elige una imagen (frente)")
            return {"CANCELLED"}
        missing = [v for v, path in views.items() if not path.is_file()]
        if missing:
            self.report({"ERROR"}, f"No existe la imagen de la vista: {', '.join(missing)}")
            return {"CANCELLED"}
        if not model_id or rt.registry.state(rt.ws, model_id) is not ModelState.INSTALLED:
            self.report({"ERROR"}, "Descarga el modelo primero")
            return {"CANCELLED"}
        preferences = prefs.get(context)
        auto_start = preferences.auto_start_engine if preferences else True
        if rt.engine_state is not EngineState.READY and not auto_start:
            self.report({"ERROR"}, "Inicia el motor primero")
            return {"CANCELLED"}
        if props.split_parts:
            if rt.registry.state(rt.ws, PARTS_MODEL) is not ModelState.INSTALLED:
                self.report({"ERROR"}, "Descarga el modelo de partes (P3-SAM) primero")
                return {"CANCELLED"}
            if not rt.manager.runner.parts_installed():
                self.report({"ERROR"}, "Instala el soporte de partes primero")
                return {"CANCELLED"}
        if props.quality == "custom":
            try:
                custom_quality(props.custom_quality())
            except PresetError as err:
                self.report({"ERROR"}, str(err))
                return {"CANCELLED"}
        try:
            target_faces = props.target_faces()
        except ValueError as err:
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}

        job = Job.create(
            rt.ws,
            image,
            model_id=model_id,
            quality=props.quality,
            target_faces=target_faces,
            seed=random.randrange(2**31) if props.random_seed else props.seed,
            name=props.object_name.strip() or None,
            parts=props.split_parts,
            upright=props.upright,
            views=views,
            custom_quality=props.custom_quality() if props.quality == "custom" else None,
            exports=props.exports(),
            project_texture=props.project_texture,
        )
        blender = Path(bpy.app.binary_path)
        rt.refresh()
        work = _generation_work(job, blender)
        return _start(self, "generate", f"Generar {job.name}", work, _on_generated)


def _generation_work(job: Job, blender: Path) -> Any:
    def work(ctx: TaskContext) -> Job:
        rt = runtime.get()
        if not rt.client.is_ready():
            ctx.report("Arrancando el motor...")
            rt.manager.start(wait=True)
            rt.refresh()

        def progress(p: Progress) -> None:
            ctx.report(p.label, p.fraction)

        pipeline = Pipeline(
            rt.ws, rt.registry, rt.presets, rt.client, blender, runner=rt.manager.runner
        )
        return pipeline.run(job, progress, ctx.cancel)

    return work


def _on_generated(task: Task) -> None:
    rt = runtime.get()
    rt.refresh()
    if task.status is TaskStatus.CANCELLED:
        rt.last_error = "Generación cancelada (puedes reanudarla en Resultados)"
        return
    if task.status is TaskStatus.FAILED:
        rt.last_error = f"{task.title}: {task.error}"
        return
    try:
        objects = import_job(bpy.context, task.result)
    except Exception as err:
        rt.last_error = f"No se pudo importar el resultado: {err}"
        return
    task.message = f"Listo: {len(objects)} objeto(s) en la escena ({task.elapsed:.0f} s)"


class THREEDAI_OT_cancel(bpy.types.Operator):
    bl_idname = "threedai.cancel"
    bl_label = "Cancelar"
    bl_description = "Cancela la tarea en curso"

    kind: StringProperty()

    def execute(self, _context: bpy.types.Context) -> set[str]:
        task = runtime.get().tasks.get(self.kind)
        if task is not None and task.running:
            task.cancel()
            task.message = "Cancelando..."
        return {"FINISHED"}


# -------------------------------------------------------------- resultados


class THREEDAI_OT_job_import(bpy.types.Operator):
    bl_idname = "threedai.job_import"
    bl_label = "Importar"
    bl_description = "Añade este resultado a la escena"

    job_id: StringProperty()

    def execute(self, context: bpy.types.Context) -> set[str]:
        rt = runtime.get()
        try:
            objects = import_job(context, Job.load(rt.ws, self.job_id))
        except Exception as err:
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}
        self.report({"INFO"}, f"{len(objects)} objeto(s) importado(s)")
        return {"FINISHED"}


class THREEDAI_OT_job_resume(bpy.types.Operator):
    bl_idname = "threedai.job_resume"
    bl_label = "Reanudar"
    bl_description = "Continúa el trabajo desde la última etapa completada"

    job_id: StringProperty()

    def execute(self, _context: bpy.types.Context) -> set[str]:
        rt = runtime.get()
        job = Job.load(rt.ws, self.job_id)
        blender = Path(bpy.app.binary_path)
        work = _generation_work(job, blender)
        return _start(self, "generate", f"Reanudar {job.name}", work, _on_generated)


class THREEDAI_OT_quality_from_preset(bpy.types.Operator):
    bl_idname = "threedai.quality_from_preset"
    bl_label = "Partir de un preset"
    bl_description = "Copia los valores de un preset a la calidad personalizada"

    preset: StringProperty()

    def execute(self, context: bpy.types.Context) -> set[str]:
        values = quality_values(runtime.get().presets.get_quality(self.preset))
        context.scene.threedai.load_quality(values)
        return {"FINISHED"}


class THREEDAI_OT_style_reset(bpy.types.Operator):
    bl_idname = "threedai.style_reset"
    bl_label = "Quitar estilo"
    bl_description = "Vuelve al aspecto original (materiales y compositor como estaban)"

    def execute(self, context: bpy.types.Context) -> set[str]:
        context.scene.threedai.style = "NONE"  # el update deshace todo
        return {"FINISHED"}


class THREEDAI_OT_dismiss_error(bpy.types.Operator):
    bl_idname = "threedai.dismiss_error"
    bl_label = "Cerrar"
    bl_description = "Oculta el mensaje de error"

    def execute(self, _context: bpy.types.Context) -> set[str]:
        rt = runtime.get()
        rt.last_error = None
        rt.version += 1
        return {"FINISHED"}


_generation_item_cache: list[tuple[str, str, str]] = []


def _generation_items(_self: Any = None, _context: Any = None) -> list[tuple[str, str, str]]:
    global _generation_item_cache
    rt = runtime.get()
    items = []
    for m in rt.registry.models.values():
        if m.family not in BUILDERS:
            continue
        installed = rt.model_states.get(m.id) is ModelState.INSTALLED
        label = m.name if installed else f"{m.name} (sin descargar)"
        items.append((m.id, label, f"{m.family} · {m.precision} · {m.license}"))
    _generation_item_cache = items or [(NO_FILE, "Sin modelos", "")]
    return _generation_item_cache


class THREEDAI_OT_select_model(bpy.types.Operator):
    bl_idname = "threedai.select_model"
    bl_label = "Elegir modelo de generación"
    bl_description = "Modelo que se usa para generar (forma + textura)"
    bl_property = "model_id"

    model_id: EnumProperty(name="Modelo", items=_generation_items)

    def execute(self, context: bpy.types.Context) -> set[str]:
        preferences = prefs.get(context)
        if preferences is not None:
            preferences.generation_model = self.model_id
        runtime.get().version += 1
        return {"FINISHED"}


class THREEDAI_OT_model_import_file(bpy.types.Operator):
    bl_idname = "threedai.model_import_file"
    bl_label = "Agregar modelo desde archivo"
    bl_description = (
        "Agrega un .safetensors de tu PC: reconoce la arquitectura leyendo solo su cabecera "
        "y, si es un archivo oficial, lo instala sin volver a descargarlo"
    )

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.safetensors", options={"HIDDEN"})
    mode: EnumProperty(
        name="Archivo original",
        items=[
            ("move", "Mover", "Lo mueve al proyecto (instantáneo en el mismo disco)"),
            ("copy", "Copiar", "Deja el original donde está (usa el doble de espacio)"),
        ],
        default="move",
    )
    name: StringProperty(name="Nombre", description="Nombre visible (vacío: el del archivo)")
    license: StringProperty(name="Licencia", default="desconocida")

    def invoke(self, context: bpy.types.Context, _event: bpy.types.Event) -> set[str]:
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, _context: bpy.types.Context) -> None:
        col = self.layout.column()
        col.prop(self, "mode")
        col.prop(self, "name")
        col.prop(self, "license")

    def execute(self, _context: bpy.types.Context) -> set[str]:
        rt = runtime.get()
        src = Path(self.filepath)
        mode, name, license_ = ImportMode(self.mode), self.name.strip(), self.license.strip()

        def work(ctx: TaskContext) -> str:
            result = import_local(
                rt.ws,
                rt.registry,
                src,
                mode=mode,
                name=name,
                license=license_,
                progress=ctx.report,
                cancel=ctx.cancel,
            )
            names = ", ".join(rt.registry.model(m).name for m in result.model_ids)
            if result.official:
                return f"Archivo oficial reconocido: {names} quedó instalado"
            return f"Agregado: {names} ({result.family}, {result.precision})"

        def done(task: Task) -> None:
            _refresh_after(task)
            rt.reload_registry()
            if task.status is TaskStatus.DONE:
                task.message = str(task.result)

        return _start(self, "download", f"Agregar {src.name}", work, done)


class THREEDAI_OT_job_open_folder(bpy.types.Operator):
    bl_idname = "threedai.job_open_folder"
    bl_label = "Abrir carpeta"
    bl_description = "Abre la carpeta del trabajo (GLB, .blend, imagen original)"

    job_id: StringProperty()

    def execute(self, _context: bpy.types.Context) -> set[str]:
        folder = runtime.get().ws.jobs_dir / self.job_id
        bpy.ops.wm.path_open(filepath=str(folder))
        return {"FINISHED"}


class THREEDAI_OT_job_delete(bpy.types.Operator):
    bl_idname = "threedai.job_delete"
    bl_label = "Borrar trabajo"
    bl_description = "Borra el trabajo y sus archivos del disco (no afecta lo ya importado)"

    job_id: StringProperty()

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, _context: bpy.types.Context) -> set[str]:
        rt = runtime.get()
        gen = rt.tasks.get("generate")
        if gen is not None and gen.running:
            self.report({"WARNING"}, "Hay una generación en curso")
            return {"CANCELLED"}
        folder = (rt.ws.jobs_dir / self.job_id).resolve()
        if folder.parent != rt.ws.jobs_dir.resolve() or not (folder / "job.json").is_file():
            self.report({"ERROR"}, "Trabajo inválido")
            return {"CANCELLED"}
        shutil.rmtree(folder)
        rt.refresh()
        return {"FINISHED"}


CLASSES = (
    THREEDAI_OT_engine_install,
    THREEDAI_OT_engine_start,
    THREEDAI_OT_engine_stop,
    THREEDAI_OT_parts_install,
    THREEDAI_OT_engine_logs,
    THREEDAI_OT_model_download,
    THREEDAI_OT_model_remove,
    THREEDAI_OT_variant_add,
    THREEDAI_OT_variant_remove,
    THREEDAI_OT_generate,
    THREEDAI_OT_cancel,
    THREEDAI_OT_job_import,
    THREEDAI_OT_job_resume,
    THREEDAI_OT_dismiss_error,
    THREEDAI_OT_style_reset,
    THREEDAI_OT_quality_from_preset,
    THREEDAI_OT_select_model,
    THREEDAI_OT_model_import_file,
    THREEDAI_OT_job_open_folder,
    THREEDAI_OT_job_delete,
)
