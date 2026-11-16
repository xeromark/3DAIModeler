"""GLB generado -> .blend listo para usar. Se ejecuta con Blender en background.

    blender --background --factory-startup --python worker/postprocess.py -- \\
        in.glb out.blend --name Modelo --target-faces 50000 [--parts labels.npz]

Pasos: importar, separar en partes (opcional), nombrar, origen abajo al centro, sombreado suave,
empaquetar texturas, verificar polígonos y guardar. Imprime una línea RESULT con JSON.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree

from projection import project_image

FACE_TOLERANCE = 0.05
UPRIGHT_MAX_TILT = 40.0
"""Solo se corrigen inclinaciones de hasta estos grados (más sería adivinar)."""
UPRIGHT_MIN_BASE = 0.08
"""La base de apoyo debe ser al menos esta fracción de la superficie de la envolvente."""


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="postprocess")
    parser.add_argument("glb", type=Path)
    parser.add_argument("blend", type=Path)
    parser.add_argument("--name", default="Modelo")
    parser.add_argument("--target-faces", type=int, default=0)
    parser.add_argument("--parts", type=Path, help="npz de parts_segment.py (centers, labels)")
    parser.add_argument(
        "--no-upright", dest="upright", action="store_false", help="no enderezar el modelo"
    )
    parser.add_argument("--export", default="", help="formatos extra separados por coma: fbx,obj")
    parser.add_argument("--project", type=Path, help="imagen recortada para proyectar (con alfa)")
    parser.add_argument("--project-fov", type=float, default=0.0, help="FOV horizontal (grados)")
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


def auto_upright(objects: list[bpy.types.Object]) -> dict[str, float]:
    """Endereza modelos que salen inclinados (Pixal3D sigue la cámara de la imagen).

    Busca en la envolvente convexa la base de apoyo más grande que mire hacia abajo (caras
    casi coplanares agrupadas) y la gira hasta quedar horizontal. Si no hay una base clara
    (p. ej. un hacha flotando) no hace nada.
    """
    bm = bmesh.new()
    for obj in objects:
        for v in obj.data.vertices:
            bm.verts.new(obj.matrix_world @ v.co)
    bmesh.ops.convex_hull(bm, input=list(bm.verts))
    down = Vector((0.0, 0.0, -1.0))
    total = sum(f.calc_area() for f in bm.faces) or 1.0
    candidates = sorted(
        (
            (f.calc_area(), f.normal.copy())
            for f in bm.faces
            if f.normal.angle(down) < math.radians(UPRIGHT_MAX_TILT)
        ),
        key=lambda c: -c[0],
    )
    bm.free()
    clusters: list[list] = []  # [área, normal ponderada]
    for area, normal in candidates:
        for cluster in clusters:
            if normal.angle(cluster[1].normalized()) < math.radians(5):
                cluster[0] += area
                cluster[1] += normal * area
                break
        else:
            clusters.append([area, normal * area])
    if not clusters:
        return {"tilt_deg": 0.0, "base_fraction": 0.0, "applied": False}
    area, weighted = max(clusters, key=lambda c: c[0])
    base_normal = weighted.normalized()
    tilt = math.degrees(base_normal.angle(down))
    info = {"tilt_deg": round(tilt, 1), "base_fraction": round(area / total, 3), "applied": False}
    if area / total < UPRIGHT_MIN_BASE or tilt < 1.0:
        return info
    rotation = base_normal.rotation_difference(down).to_matrix().to_4x4()
    lo, hi = world_bounds(objects)
    center = (lo + hi) / 2
    pivot = Matrix.Translation(center) @ rotation @ Matrix.Translation(-center)
    for obj in objects:
        to_world = obj.matrix_world.copy()
        obj.data.transform(to_world.inverted() @ pivot @ to_world)
    info["applied"] = True
    return info


def texture_roles() -> dict[str, str]:
    """Nombre de imagen -> función (color, normal, orm) según cómo se conecta al material."""
    roles: dict[str, str] = {}
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
        for link in mat.node_tree.links:
            if link.from_node.type != "TEX_IMAGE" or link.from_node.image is None:
                continue
            target = link.to_node
            if target.type == "BSDF_PRINCIPLED" and link.to_socket.name == "Base Color":
                role = "color"
            elif target.type == "NORMAL_MAP":
                role = "normal"
            elif target.type == "SEPARATE_COLOR":
                role = "orm"  # oclusión (R), rugosidad (G), metálico (B)
            else:
                continue
            roles[link.from_node.image.name] = role
    return roles


def export_formats(blend: Path, formats: list[str]) -> list[str]:
    """Exporta la escena junto al .blend (mismo nombre, otra extensión)."""
    done = []
    if not formats:
        return done
    # Las texturas están empaquetadas en el .blend: los exportadores necesitan archivos.
    # Se guardan junto al modelo con nombres según su función (color, normal, orm).
    roles = texture_roles()
    for image in bpy.data.images:
        if image.packed_file is None:
            continue
        role = roles.get(image.name, bpy.path.clean_name(image.name))
        image.filepath_raw = str(blend.with_name(f"{blend.stem}_{role}.png"))
        image.file_format = "PNG"
        image.save()
    bpy.ops.object.select_all(action="SELECT")
    for fmt in formats:
        target = blend.with_suffix(f".{fmt}")
        if fmt == "fbx":
            bpy.ops.export_scene.fbx(
                filepath=str(target), path_mode="COPY", embed_textures=True, use_selection=False
            )
        elif fmt == "obj":
            bpy.ops.wm.obj_export(filepath=str(target), path_mode="RELATIVE", export_materials=True)
        else:
            continue
        done.append(fmt)
    return done


def gltf_to_blender(points: np.ndarray) -> np.ndarray:
    """glTF es Y-arriba; Blender, Z-arriba: (x, y, z) -> (x, -z, y)."""
    return np.stack([points[:, 0], -points[:, 2], points[:, 1]], axis=1)


def split_parts(obj: bpy.types.Object, labels_file: Path) -> list[bpy.types.Object]:
    """Separa `obj` en un objeto por parte. Cada cara toma la etiqueta de la cara
    segmentada más cercana, así no importa si el importador reordenó las caras."""
    data = np.load(labels_file)
    centers = gltf_to_blender(data["centers"].astype(np.float64))
    labels = data["labels"]
    tree = KDTree(len(centers))
    for i, c in enumerate(centers):
        tree.insert(c, i)
    tree.balance()

    mesh = obj.data
    to_world = obj.matrix_world
    face_labels = [int(labels[tree.find(to_world @ p.center)[1]]) for p in mesh.polygons]
    unique, counts = np.unique(face_labels, return_counts=True)
    if len(unique) <= 1:
        return [obj]
    order = [int(u) for u in unique[np.argsort(-counts)]]  # la parte más grande primero

    attr = mesh.attributes.new("part", "INT", "FACE")
    attr.data.foreach_set("value", face_labels)

    parts = [obj]
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = obj
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)  # selección por caras
    for label in order[1:]:
        before = set(bpy.context.scene.objects)
        source = parts[0]
        values = [0] * len(source.data.polygons)
        source.data.attributes["part"].data.foreach_get("value", values)
        source.data.vertices.foreach_set("select", [False] * len(source.data.vertices))
        source.data.edges.foreach_set("select", [False] * len(source.data.edges))
        source.data.polygons.foreach_set("select", [v == label for v in values])
        bpy.context.view_layer.objects.active = source
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.separate(type="SELECTED")
        bpy.ops.object.mode_set(mode="OBJECT")
        parts += [o for o in bpy.context.scene.objects if o not in before]
    for part in parts:
        part.data.attributes.remove(part.data.attributes["part"])
    return parts


def origins_to_centers(objects: list[bpy.types.Object]) -> None:
    """Cada parte con su origen en el centro de su caja, para moverla o rotarla fácil."""
    for obj in objects:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")


def shade_smooth(objects: list[bpy.types.Object]) -> None:
    for obj in objects:
        for poly in obj.data.polygons:
            poly.use_smooth = True


def name_objects(objects: list[bpy.types.Object], name: str) -> None:
    for i, obj in enumerate(objects, start=1):
        obj.name = name if len(objects) == 1 else f"{name}.parte_{i:02d}"
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
    projection: dict = {"applied": False}
    if args.project is not None and args.project_fov > 0 and len(objects) == 1:
        # Antes de enderezar o mover: la cámara de Pixal3D está en las coordenadas originales.
        projection = project_image(objects[0], args.project, args.project_fov)
    if args.parts is not None and len(objects) == 1:
        objects = split_parts(objects[0], args.parts)
    upright = auto_upright(objects) if args.upright else {"applied": False}
    origin_to_bottom_center(objects)
    if len(objects) > 1:
        origins_to_centers(objects)
    shade_smooth(objects)
    name_objects(objects, args.name)
    packed = pack_images()

    faces = sum(len(o.data.polygons) for o in objects)
    lo, hi = world_bounds(objects)
    within = True
    if args.target_faces:
        within = abs(faces - args.target_faces) <= args.target_faces * FACE_TOLERANCE

    args.blend.parent.mkdir(parents=True, exist_ok=True)
    bpy.context.preferences.filepaths.save_version = 0  # sin respaldos .blend1
    bpy.ops.wm.save_as_mainfile(filepath=str(args.blend), compress=True)
    exported = export_formats(args.blend, [f for f in args.export.split(",") if f])

    result = {
        "objects": [o.name for o in objects],
        "faces": faces,
        "target_faces": args.target_faces,
        "faces_within_tolerance": within,
        "upright": upright,
        "projection": projection,
        "exported": exported,
        "packed_images": packed,
        "materials": material_report(objects),
        "dimensions": [round(v, 4) for v in (hi - lo)],
    }
    print("RESULT " + json.dumps(result))


main()
