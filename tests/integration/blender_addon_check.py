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

if image is not None:
    props = bpy.context.scene.threedai
    props.image = str(image)
    props.model_id = "pixal3d-int8"
    props.quality = "draft"
    props.faces = "custom"
    props.faces_custom = 5000
    props.object_name = "PruebaAddon"
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
    obj = bpy.data.objects.get("PruebaAddon")
    assert obj is not None, "el resultado no se importó"
    assert obj.name in bpy.context.scene.objects
    materials = [m.name for m in obj.data.materials]
    print("IMPORTED", obj.name, len(obj.data.polygons), "caras", materials)

bpy.ops.preferences.addon_disable(module=module)
print("ADDON_CHECK_OK")
