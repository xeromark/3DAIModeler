"""Prueba el addon dentro de Blender (background), de punta a punta.

    blender --background --factory-startup --python tests/integration/blender_addon_check.py \\
        -- <workspace> [imagen]

Sin imagen: solo registro, estado del motor y logs. Con imagen: también genera (draft, 5k caras)
e importa el resultado en la escena. Termina con código != 0 si algo falla.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import bpy

argv = sys.argv[sys.argv.index("--") + 1 :]
workspace = Path(argv[0]).resolve()
image = Path(argv[1]).resolve() if len(argv) > 1 else None

# Repositorio local que apunta a src/ (no se guarda: --factory-startup).
repo = bpy.context.preferences.extensions.repos.new(
    name="3DAIModeler test", module="threedai_test", custom_directory=str(workspace / "src")
)
bpy.ops.extensions.repo_refresh_all()
module = "bl_ext.threedai_test.threedai_modeler"
bpy.ops.preferences.addon_enable(module=module)
assert module in bpy.context.preferences.addons, "el addon no se habilitó"
# No detener el motor al deshabilitar el addon al final de la prueba.
bpy.context.preferences.addons[module].preferences.stop_engine_on_exit = False

ui = sys.modules[module + ".ui"]
runtime = sys.modules[module + ".ui.runtime"]
rt = runtime.get()
assert hasattr(bpy.types, "THREEDAI_PT_generate"), "paneles no registrados"
assert rt.ws.root == workspace, rt.ws.root


def pump(seconds: float, until: object = None) -> None:
    """Simula el timer de la interfaz (en background no corre solo)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ui._tick()
        if until is not None and until():  # type: ignore[operator]
            return
        time.sleep(0.5)


pump(10, until=lambda: rt.engine_message != "Consultando...")
print("ENGINE", rt.engine_state, rt.engine_message)
print("MODELS", {k: v.value for k, v in rt.model_states.items()})
assert bpy.ops.threedai.engine_logs() == {"FINISHED"}


# Dibujo de todos los paneles con un layout simulado (en background no hay interfaz).
class FakeLayout:
    def __getattr__(self, name: str) -> object:
        if name in ("enabled", "alert", "scale_y", "active"):
            return True
        return lambda *a, **k: FakeLayout()

    def __setattr__(self, name: str, value: object) -> None:
        pass


class FakeRegion:
    width = 300


class FakeContext:
    def __getattr__(self, name: str) -> object:
        return getattr(bpy.context, name)

    region = FakeRegion()


panels = sys.modules[module + ".ui.panels"]
for show_parts in (False, True):
    bpy.context.scene.threedai.split_parts = show_parts
    bpy.context.scene.threedai.image = str(workspace / "engine" / "samples" / "viking_axe.png")
    for cls in panels.CLASSES:

        class FakePanel:
            layout = FakeLayout()
            panel_cls = cls

            def __getattr__(self, name: str) -> object:
                return getattr(self.panel_cls, name)

        cls.draw(FakePanel(), FakeContext())
bpy.context.scene.threedai.split_parts = False
print("PANELS_DRAW_OK", len(panels.CLASSES))

assert bpy.ops.threedai.select_model(model_id="trellis2-int8") == {"FINISHED"}
prefs_mod = sys.modules[module + ".ui.prefs"]
assert prefs_mod.generation_model_id() == "trellis2-int8"
assert bpy.ops.threedai.select_model(model_id="pixal3d-int8") == {"FINISHED"}
print("SELECT_MODEL_OK", prefs_mod.generation_model_id())
print("USAGE", rt.usage, rt.engine_ram)

# Variante de modelo: consulta real a Hugging Face (sin descargar los pesos).
user_registry = rt.ws.user_registry_file
backup = user_registry.read_bytes() if user_registry.exists() else None
try:
    ops_mod = sys.modules[module + ".ui.ops"]
    files = ops_mod._repo_file_items(type("P", (), {"repo": "Comfy-Org/TRELLIS.2"})(), None)
    assert any("trellis_2_bf16" in f[0] for f in files), files
    print("HF_FILES", [f[0] for f in files])
    assert bpy.ops.threedai.variant_add(
        "EXEC_DEFAULT",
        family="trellis2",
        repo="Comfy-Org/TRELLIS.2",
        path="diffusion_models/trellis_2_bf16.safetensors",
        name="bf16 prueba",
        precision="bf16",
        license="MIT",
    ) == {"FINISHED"}
    variant_task = rt.tasks.get("download")
    pump(60, until=lambda: not variant_task.running)
    pump(1)
    assert variant_task.status.value == "done", variant_task.error
    model = rt.registry.model("trellis2-bf16-prueba")
    assert model.user_added and model.files[0].startswith("user-")
    print("VARIANT", model.id, rt.registry.model_size(model.id) // 10**9, "GB")
    assert bpy.ops.threedai.variant_remove("EXEC_DEFAULT", model_id=model.id) == {"FINISHED"}
    assert "trellis2-bf16-prueba" not in rt.registry.models
finally:
    if backup is None:
        user_registry.unlink(missing_ok=True)
    else:
        user_registry.write_bytes(backup)

if image is not None:
    props = bpy.context.scene.threedai
    props.image = str(image)
    bpy.context.preferences.addons[module].preferences.generation_model = "pixal3d-int8"
    props.quality = "draft"
    props.faces = "custom"
    props.faces_custom = 5000
    props.object_name = "PruebaAddon"
    props.split_parts = rt.parts_installed and rt.model_states.get("p3sam").value == "installed"
    print("PARTS", props.split_parts)
    assert bpy.ops.threedai.generate() == {"FINISHED"}
    task = rt.tasks.get("generate")
    last = ""
    deadline = time.monotonic() + 900
    while task.running and time.monotonic() < deadline:
        ui._tick()
        if task.message != last:
            last = task.message
            print(f"  {task.elapsed:5.0f}s {last}")
        time.sleep(0.5)
    pump(2)
    print("TASK", task.status, task.error, task.message)
    assert task.status.value == "done", task.error
    imported = [o for o in bpy.context.scene.objects if o.name.startswith("PruebaAddon")]
    assert imported, "el resultado no se importó"
    if props.split_parts:
        assert len(imported) > 1, "se pidieron partes pero llegó un solo objeto"
    faces = sum(len(o.data.polygons) for o in imported)
    print("IMPORTED", [o.name for o in imported], faces, "caras")

bpy.ops.preferences.addon_disable(module=module)
print("ADDON_CHECK_OK")
