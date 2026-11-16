"""Proyecta la imagen original sobre el modelo (texturas con la nitidez de la imagen).

Pixal3D genera el modelo alineado píxel a píxel con la imagen recortada que recibió. Su cámara,
en las coordenadas que deja el importador glTF de Blender, está en (0, -d, 0) mirando hacia +Y
con +Z arriba, d = 0.5 / tan(fov / 2) (comfy/ldm/trellis2: _PROJ_FRONT_VIEW_TRANSFORM).

Para cada esquina de cara se calcula dónde cae en la imagen y cuánto se puede confiar en ese
píxel (peso): visible desde la cámara (rayo sin obstáculos), dentro de la máscara del objeto y
de frente a la cámara (los ángulos rasantes estiran la imagen). Luego Cycles hornea la mezcla
"imagen proyectada / textura generada" sobre la textura de color original.
"""

from __future__ import annotations

import math
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

PROJ_UV = "3dai_proj"
PROJ_WEIGHT = "3dai_proj_w"
MIN_FACING = 0.25
"""Por debajo de este coseno (cara casi de perfil) no se proyecta."""
FULL_FACING = 0.6
"""Desde este coseno se proyecta con peso completo."""
ERODE_PX = 4
"""Margen dentro de la máscara: evita traer el fondo negro en los bordes."""
MIN_ALIGNMENT = 0.85
"""Si menos de esta fracción de vértices visibles cae dentro de la máscara, la cámara no
coincide con el modelo: se cancela la proyección para no estropear la textura."""


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _load_mask(image: bpy.types.Image) -> tuple[np.ndarray, np.ndarray]:
    """Máscara del objeto (alfa) y su versión erosionada, con el origen arriba a la izquierda."""
    w, h = image.size
    px = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)[::-1]  # Blender guarda las filas de abajo hacia arriba
    alpha = px[..., 3] if image.alpha_mode != "NONE" else px[..., :3].max(axis=-1)
    full = mask = alpha > 0.5
    for _ in range(ERODE_PX):  # erosión simple con vecinos en cruz
        m = mask.copy()
        m[1:] &= mask[:-1]
        m[:-1] &= mask[1:]
        m[:, 1:] &= mask[:, :-1]
        m[:, :-1] &= mask[:, 1:]
        mask = m
    return full, mask


def _vertex_data(obj: bpy.types.Object) -> np.ndarray:
    n = len(obj.data.vertices)
    co = np.empty(n * 3, dtype=np.float64)
    obj.data.vertices.foreach_get("co", co)
    co = co.reshape(n, 3)
    mw = np.array(obj.matrix_world, dtype=np.float64)
    return co @ mw[:3, :3].T + mw[:3, 3]


def project_image(obj: bpy.types.Object, image_path: Path, fov_deg: float) -> dict[str, object]:
    """Calcula UV proyectadas y pesos en `obj`, y hornea la mezcla en su textura de color."""
    proj_img = bpy.data.images.load(str(image_path), check_existing=False)
    proj_img.alpha_mode = "STRAIGHT"
    full_mask, mask = _load_mask(proj_img)
    res_y, res_x = mask.shape

    tan_half = math.tan(math.radians(fov_deg) / 2.0)
    cam = np.array([0.0, -0.5 / tan_half, 0.0])

    verts = _vertex_data(obj)
    depth = verts[:, 1] - cam[1]
    u = 0.5 + verts[:, 0] / (2.0 * tan_half * depth)
    v_up = 0.5 + verts[:, 2] / (2.0 * tan_half * depth)  # UV de Blender: v hacia arriba

    # Visibilidad por vértice: rayo desde la cámara hasta el vértice sin chocar antes.
    depsgraph = bpy.context.evaluated_depsgraph_get()
    bvh = BVHTree.FromObject(obj, depsgraph)
    size = float(np.ptp(verts, axis=0).max()) or 1.0
    eps = size * 2e-3
    origin = Vector(cam)
    visible = np.zeros(len(verts), dtype=bool)
    # Solo vale la pena lanzar rayos desde vértices que miran hacia la cámara.
    vnormals = np.empty(len(verts) * 3)
    obj.data.vertices.foreach_get("normal", vnormals)
    to_cam_v = cam - verts
    to_cam_v /= np.linalg.norm(to_cam_v, axis=1, keepdims=True)
    candidates = np.flatnonzero((vnormals.reshape(-1, 3) * to_cam_v).sum(axis=1) > MIN_FACING / 2)
    for i in candidates:
        p = verts[i]
        target = Vector(p)
        direction = target - origin
        dist = direction.length
        hit, *_ = bvh.ray_cast(origin, direction / dist, dist - eps)
        visible[i] = hit is None

    # Dentro de la máscara del objeto (la erosionada decide el peso; la completa, la alineación).
    px = np.clip((u * res_x).astype(int), 0, res_x - 1)
    py = np.clip(((1.0 - v_up) * res_y).astype(int), 0, res_y - 1)
    in_frame = (u >= 0) & (u <= 1) & (v_up >= 0) & (v_up <= 1)
    inside = mask[py, px] & in_frame
    aligned = full_mask[py, px] & in_frame
    alignment = float(aligned.mean())
    info: dict[str, object] = {
        "visible_vertices": round(float(visible.mean()), 3),
        "alignment": round(alignment, 3),
        "applied": False,
    }
    if alignment < MIN_ALIGNMENT:
        bpy.data.images.remove(proj_img)
        info["reason"] = "la cámara no coincide con el modelo"
        return info

    # Orientación de cada cara hacia la cámara.
    mesh = obj.data
    n_faces = len(mesh.polygons)
    normals = np.empty(n_faces * 3)
    centers = np.empty(n_faces * 3)
    mesh.polygons.foreach_get("normal", normals)
    mesh.polygons.foreach_get("center", centers)
    normals, centers = normals.reshape(-1, 3), centers.reshape(-1, 3)
    to_cam = cam - centers
    to_cam /= np.linalg.norm(to_cam, axis=1, keepdims=True)
    facing = _smoothstep(MIN_FACING, FULL_FACING, (normals * to_cam).sum(axis=1))

    # Pesos y UV por esquina de cara.
    n_loops = len(mesh.loops)
    loop_vert = np.empty(n_loops, dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", loop_vert)
    loop_face = np.repeat(np.arange(n_faces), [len(p.vertices) for p in mesh.polygons])
    weight = (visible & inside)[loop_vert].astype(np.float32) * facing[loop_face]

    uv_layer = mesh.uv_layers.new(name=PROJ_UV)
    uv_layer.data.foreach_set("uv", np.stack([u, v_up], axis=1)[loop_vert].ravel())
    attr = mesh.color_attributes.new(PROJ_WEIGHT, "FLOAT_COLOR", "CORNER")
    rgba = np.repeat(weight[:, None], 4, axis=1)
    rgba[:, 3] = 1.0
    attr.data.foreach_set("color", rgba.ravel())

    baked = _bake_mix(obj, proj_img)
    info.update(
        applied=baked,
        projected_fraction=round(float((weight > 0.5).mean()), 3),
    )
    mesh.uv_layers.remove(mesh.uv_layers[PROJ_UV])
    mesh.color_attributes.remove(mesh.color_attributes[PROJ_WEIGHT])
    bpy.data.images.remove(proj_img)
    return info


def _color_texture_node(material: bpy.types.Material) -> bpy.types.Node | None:
    bsdf = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None or not bsdf.inputs["Base Color"].is_linked:
        return None
    node = bsdf.inputs["Base Color"].links[0].from_node
    return node if node.type == "TEX_IMAGE" and node.image is not None else None


def _bake_mix(obj: bpy.types.Object, proj_img: bpy.types.Image) -> bool:
    """Hornea mezcla(textura generada, imagen proyectada, peso) sobre la textura de color."""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    scene.render.bake.margin = 16
    scene.render.bake.use_clear = True

    materials = {s.material for s in obj.material_slots if s.material and s.material.use_nodes}
    work = []
    for material in materials:
        color_node = _color_texture_node(material)
        if color_node is None:
            continue
        tree = material.node_tree
        original = color_node.image
        target = bpy.data.images.new(
            f"{original.name}_proj", original.size[0], original.size[1], alpha=False
        )
        target.colorspace_settings.name = original.colorspace_settings.name

        made = []

        def new(kind: str, tree: bpy.types.NodeTree = tree, made: list = made) -> bpy.types.Node:
            node = tree.nodes.new(kind)
            node.label = "3DAI proj"
            made.append(node)
            return node

        proj_uv = new("ShaderNodeUVMap")
        proj_uv.uv_map = PROJ_UV
        proj_tex = new("ShaderNodeTexImage")
        proj_tex.image = proj_img
        proj_tex.extension = "CLIP"
        weight = new("ShaderNodeAttribute")
        weight.attribute_name = PROJ_WEIGHT
        mix = new("ShaderNodeMix")
        mix.data_type = "RGBA"
        emission = new("ShaderNodeEmission")
        output = new("ShaderNodeOutputMaterial")
        bake_node = new("ShaderNodeTexImage")
        bake_node.image = target

        tree.links.new(proj_uv.outputs["UV"], proj_tex.inputs["Vector"])
        tree.links.new(weight.outputs["Fac"], mix.inputs["Factor"])
        tree.links.new(color_node.outputs["Color"], mix.inputs["A"])
        tree.links.new(proj_tex.outputs["Color"], mix.inputs["B"])
        tree.links.new(mix.outputs["Result"], emission.inputs["Color"])
        tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
        previous = [n for n in tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output]
        output.is_active_output = True
        tree.nodes.active = bake_node
        work.append((material, color_node, target, made, previous))

    if not work:
        return False
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.bake(type="EMIT")

    for material, color_node, target, made, previous in work:
        color_node.image = target  # la textura de color pasa a ser la horneada
        target.pack()
        for node in made:
            material.node_tree.nodes.remove(node)
        for node in previous:
            node.is_active_output = True
    return True
