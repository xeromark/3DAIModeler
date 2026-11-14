"""Renderiza vistas previas (4 ángulos en una imagen) con Blender en background.

    blender --background --factory-startup --python worker/render_preview.py -- \\
        in.glb|in.blend out.png [px] [--color-parts]

--color-parts pinta cada objeto de un color distinto (para revisar la separación en partes).

Sirve para revisar resultados y para el benchmark sin abrir la interfaz de Blender.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def parse_args() -> tuple[Path, Path, int, bool]:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    color_parts = "--color-parts" in argv
    argv = [a for a in argv if a != "--color-parts"]
    if len(argv) < 2:
        raise SystemExit("uso: ... -- in.glb|in.blend out.png [px] [--color-parts]")
    size = int(argv[2]) if len(argv) > 2 else 512
    return Path(argv[0]).resolve(), Path(argv[1]).resolve(), size, color_parts


def load_scene(path: Path) -> list[bpy.types.Object]:
    if path.suffix == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(path))
        for obj in list(bpy.context.scene.objects):
            if obj.type in ("LIGHT", "CAMERA"):
                bpy.data.objects.remove(obj)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(path))
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


PALETTE = [
    (0.90, 0.30, 0.25),
    (0.25, 0.60, 0.90),
    (0.35, 0.80, 0.35),
    (0.95, 0.75, 0.20),
    (0.70, 0.40, 0.85),
    (0.20, 0.80, 0.80),
    (0.95, 0.50, 0.70),
    (0.55, 0.45, 0.30),
]


def color_parts(objects: list[bpy.types.Object]) -> None:
    for i, obj in enumerate(objects):
        mat = bpy.data.materials.new(f"parte_{i}")
        mat.diffuse_color = (*PALETTE[i % len(PALETTE)], 1.0)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (*PALETTE[i % len(PALETTE)], 1.0)
        obj.data.materials.clear()
        obj.data.materials.append(mat)


def bounds(objects: list[bpy.types.Object]) -> tuple[Vector, float]:
    corners = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
    lo = Vector(tuple(min(c[i] for c in corners) for i in range(3)))
    hi = Vector(tuple(max(c[i] for c in corners) for i in range(3)))
    return (lo + hi) / 2, (hi - lo).length / 2


def setup_scene(center: Vector, radius: float, size: int) -> bpy.types.Object:
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE"
    except TypeError:  # Blender 4.2-4.x
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"

    world = bpy.data.worlds.new("preview")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.8, 0.8, 0.82, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 1.0
    scene.world = world

    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(50), 0, math.radians(30))
    scene.collection.objects.link(sun)

    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.lens = 50
    scene.collection.objects.link(cam)
    scene.camera = cam
    return cam


def place_camera(cam: bpy.types.Object, center: Vector, radius: float, yaw_deg: float) -> None:
    fov = cam.data.angle
    dist = radius / math.sin(fov / 2) * 1.05
    yaw, pitch = math.radians(yaw_deg), math.radians(20)
    offset = Vector(
        (math.sin(yaw) * math.cos(pitch), -math.cos(yaw) * math.cos(pitch), math.sin(pitch))
    )
    cam.location = center + offset * dist
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()


def main() -> None:
    src, out, size, paint = parse_args()
    objects = load_scene(src)
    if not objects:
        raise SystemExit(f"{src}: sin mallas")
    if paint:
        color_parts(objects)
    center, radius = bounds(objects)
    cam = setup_scene(center, radius, size)

    tiles = []
    for i, yaw in enumerate((0, 90, 180, 270)):
        place_camera(cam, center, radius, yaw)
        tile = out.with_name(f"{out.stem}_{i}.png")
        bpy.context.scene.render.filepath = str(tile)
        bpy.ops.render.render(write_still=True)
        tiles.append(tile)

    # Juntar las 4 vistas en una sola imagen 2x2.
    images = [bpy.data.images.load(str(t)) for t in tiles]
    sheet = bpy.data.images.new("sheet", size * 2, size * 2, alpha=False)
    pixels = [0.0] * (size * 2 * size * 2 * 4)
    for idx, img in enumerate(images):
        ox, oy = (idx % 2) * size, (1 - idx // 2) * size
        src_px = list(img.pixels)
        for y in range(size):
            row = (y * size) * 4
            dst = ((oy + y) * size * 2 + ox) * 4
            pixels[dst : dst + size * 4] = src_px[row : row + size * 4]
    sheet.pixels = pixels
    sheet.filepath_raw = str(out)
    sheet.file_format = "PNG"
    sheet.save()
    for t in tiles:
        t.unlink()

    faces = sum(len(o.data.polygons) for o in objects)
    print(f"PREVIEW {out} objetos={len(objects)} caras={faces}")


main()
