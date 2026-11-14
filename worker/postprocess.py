"""GLB generado -> .blend listo para usar. Se ejecuta con Blender en background.

    blender --background --factory-startup --python worker/postprocess.py -- \\
        in.glb out.blend --name Modelo --target-faces 50000

Pasos: importar, nombrar, origen abajo al centro, sombreado suave, empaquetar texturas,
verificar polígonos y guardar. Imprime una línea RESULT con JSON para el pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

FACE_TOLERANCE = 0.05


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="postprocess")
    parser.add_argument("glb", type=Path)
    parser.add_argument("blend", type=Path)
    parser.add_argument("--name", default="Modelo")
    parser.add_argument("--target-faces", type=int, default=0)
    return parser.parse_args(argv)


def import_glb(path: Path) -> list[bpy.types.Object]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(path))
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit(f"{path}: el GLB no tiene mallas")
    return meshes


def flatten_hierarchy(objects: list[bpy.types.Object]) -> None:
    """Quita los empties del glTF manteniendo la transformación de cada malla."""
    for obj in objects:
        matrix = obj.matrix_world.copy()
        obj.parent = None
        obj.matrix_world = matrix
    for obj in list(bpy.context.scene.objects):
        if obj.type == "EMPTY" and not obj.children:
            bpy.data.objects.remove(obj)


def apply_transforms(objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)


def world_bounds(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    corners = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
    lo = Vector(tuple(min(c[i] for c in corners) for i in range(3)))
    hi = Vector(tuple(max(c[i] for c in corners) for i in range(3)))
    return lo, hi


def origin_to_bottom_center(objects: list[bpy.types.Object]) -> None:
    """Coloca el conjunto apoyado en el suelo (Z=0) y centrado en X/Y."""
    lo, hi = world_bounds(objects)
    pivot = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    for obj in objects:
        to_world = obj.matrix_world.copy()
        obj.data.transform(to_world.inverted() @ Matrix.Translation(-pivot) @ to_world)


def shade_smooth(objects: list[bpy.types.Object]) -> None:
    for obj in objects:
        for poly in obj.data.polygons:
            poly.use_smooth = True


def name_objects(objects: list[bpy.types.Object], name: str) -> None:
    for i, obj in enumerate(objects):
        obj.name = name if len(objects) == 1 else f"{name}.{i:02d}"
        obj.data.name = obj.name
        for slot in obj.material_slots:
            if slot.material is not None and slot.material.users == 1:
                slot.material.name = f"{obj.name}_material"


def pack_images() -> int:
    count = 0
    for image in bpy.data.images:
        if image.source == "FILE" and not image.packed_file:
            image.pack()
            count += 1
        elif image.packed_file:
            count += 1
    return count


def material_report(objects: list[bpy.types.Object]) -> dict[str, list[str]]:
    """Qué canales PBR llegan conectados al Principled BSDF de cada material."""
    report: dict[str, list[str]] = {}
    for obj in objects:
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or not mat.use_nodes:
                continue
            bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            if bsdf is None:
                continue
            linked = [s.name for s in bsdf.inputs if s.is_linked]
            report[mat.name] = linked
    return report


def main() -> None:
    args = parse_args()
    objects = import_glb(args.glb)
    flatten_hierarchy(objects)
    apply_transforms(objects)
    origin_to_bottom_center(objects)
    shade_smooth(objects)
    name_objects(objects, args.name)
    packed = pack_images()

    faces = sum(len(o.data.polygons) for o in objects)
    lo, hi = world_bounds(objects)
    within = True
    if args.target_faces:
        within = abs(faces - args.target_faces) <= args.target_faces * FACE_TOLERANCE

    args.blend.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.blend), compress=True)

    result = {
        "objects": [o.name for o in objects],
        "faces": faces,
        "target_faces": args.target_faces,
        "faces_within_tolerance": within,
        "packed_images": packed,
        "materials": material_report(objects),
        "dimensions": [round(v, 4) for v in (hi - lo)],
    }
    print("RESULT " + json.dumps(result))


main()
