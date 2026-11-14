"""Prueba los estilos visuales en Blender (background) y renderiza una hoja de comparación.

    blender --background --factory-startup --python tests/integration/blender_styles_check.py \\
        -- <workspace> <job_id> <salida.png>

Aplica cada estilo a un trabajo importado, renderiza, y verifica que "Original" deja los
materiales, modificadores y compositor como estaban.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1 :]
workspace, job_id, out = Path(argv[0]).resolve(), argv[1], Path(argv[2]).resolve()

bpy.context.preferences.extensions.repos.new(
    name="3DAIModeler test", module="threedai_test", custom_directory=str(workspace / "src")
)
bpy.ops.extensions.repo_refresh_all()
module = "bl_ext.threedai_test.threedai_modeler"
bpy.ops.preferences.addon_enable(module=module)
bpy.context.preferences.addons[module].preferences.stop_engine_on_exit = False
ops = sys.modules[module + ".ui.ops"]
styles = sys.modules[module + ".ui.styles"]
runtime = sys.modules[module + ".ui.runtime"]
Job = sys.modules[module + ".core.jobs"].Job

scene = bpy.context.scene
for obj in list(scene.objects):
    bpy.data.objects.remove(obj)
objects = ops.import_job(bpy.context, Job.load(runtime.get().ws, job_id))
assert objects, "no se importó nada"


def snapshot() -> tuple:
    mats = {s.material for o in objects for s in o.material_slots if s.material}
    toon_nodes = sum(1 for m in mats for n in m.node_tree.nodes if n.label.startswith("3DAI"))
    outlines = sum(1 for o in objects for m in o.modifiers if m.name == styles.OUTLINE_MOD)
    slots = sum(len(o.material_slots) for o in objects)
    active = tuple(
        sorted(
            n.name
            for m in mats
            for n in m.node_tree.nodes
            if n.type == "OUTPUT_MATERIAL" and n.is_active_output
        )
    )
    return toon_nodes, outlines, slots, active, scene.compositing_node_group


before = snapshot()

# Cámara y luz encuadrando el modelo.
corners = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
lo = Vector([min(c[i] for c in corners) for i in range(3)])
hi = Vector([max(c[i] for c in corners) for i in range(3)])
center, radius = (lo + hi) / 2, (hi - lo).length / 2
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
scene.collection.objects.link(cam)
scene.camera = cam
direction = Vector((0.35, -1.0, 0.12)).normalized()
cam.location = center + direction * radius / math.sin(cam.data.angle / 2) * 1.05
cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
sun.data.energy = 3.0
sun.rotation_euler = (math.radians(45), 0, math.radians(35))
scene.collection.objects.link(sun)
world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.75, 0.75, 0.78, 1)
scene.world = world
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x, scene.render.resolution_y = 400, 520
scene.view_settings.view_transform = "Standard"

tiles = []
for style, _label, *_ in styles.STYLE_ITEMS:
    scene.threedai.style = style  # el update aplica el estilo
    tile = out.with_name(f"{out.stem}_{style}.png")
    scene.render.filepath = str(tile)
    bpy.ops.render.render(write_still=True)
    tiles.append(tile)
    print("STYLE", style, "OK", snapshot()[:3])

scene.threedai.style = "NONE"
after = snapshot()
assert after == before, f"no se restauró: antes={before} después={after}"
print("RESTORE_OK", after[:3])

# Hoja de comparación en una fila.
w, h = scene.render.resolution_x, scene.render.resolution_y
images = [bpy.data.images.load(str(t)) for t in tiles]
sheet = bpy.data.images.new("sheet", w * len(images), h, alpha=False)
pixels = [0.0] * (w * len(images) * h * 4)
for idx, img in enumerate(images):
    src = list(img.pixels)
    for y in range(h):
        dst = (y * w * len(images) + idx * w) * 4
        pixels[dst : dst + w * 4] = src[y * w * 4 : (y + 1) * w * 4]
sheet.pixels = pixels
sheet.filepath_raw = str(out)
sheet.file_format = "PNG"
sheet.save()
for t in tiles:
    t.unlink()
print("SHEET", out)
print("STYLES_CHECK_OK")
