"""Proyecta las imágenes de entrada sobre el modelo (texturas con la nitidez de las imágenes).

Pixal3D genera el modelo alineado con las imágenes recortadas que recibió. Sus cámaras, en las
coordenadas que deja el importador glTF de Blender (Z arriba), orbitan el origen a la altura del
centro mirando hacia él (comfy/ldm/trellis2 y nodes_trellis2):

- una imagen: frente, distancia d = 0.5 / tan(fov / 2), fov estimado por MoGe;
- multivista: frente/izquierda/espalda/derecha (azimut 0/90/180/270), d = 1.1 * 0.5 / tan(fov / 2).

Por cada cámara y cada esquina de cara se calcula dónde cae en su imagen y un peso de confianza:
visible desde la cámara (rayo sin obstáculos), dentro de la máscara del objeto y de frente a la
cámara (los ángulos rasantes estiran la imagen). Los pesos se normalizan entre cámaras y Cycles
hornea "textura generada x (1 - suma) + suma de imágenes x peso" sobre la textura de color.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

MIN_FACING = 0.25
"""Por debajo de este coseno (cara casi de perfil) no se proyecta."""
FULL_FACING = 0.6
"""Desde este coseno se proyecta con peso completo."""
ERODE_PX = 4
"""Margen dentro de la máscara: evita traer el fondo en los bordes."""
MIN_ALIGNMENT = 0.85
"""Si menos de esta fracción de vértices cae dentro de la silueta, esa cámara no coincide con
el modelo y se descarta para no estropear la textura."""
GEN_WEIGHT = "3dai_gen_w"


@dataclass(frozen=True)
class Camera:
    image: Path
    fov_deg: float
    distance: float
    azimuth_deg: float = 0.0
    name: str = "front"

    @property
    def axes(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Posición, derecha, arriba y "atrás" (de la escena hacia la cámara)."""
        az = math.radians(self.azimuth_deg)
        back = np.array([math.sin(az), -math.cos(az), 0.0])
        right = np.array([math.cos(az), math.sin(az), 0.0])
        up = np.cross(back, right)
        return back * self.distance, right, up, back


def load_cameras(spec: Path) -> list[Camera]:
    """projection.json del pipeline: lista de {image, fov, distance, azimuth, view}."""
    data = json.loads(spec.read_text())
    return [
        Camera(
            image=(spec.parent / item["image"]).resolve(),
            fov_deg=float(item["fov"]),
            distance=float(item["distance"]),
            azimuth_deg=float(item.get("azimuth", 0.0)),
            name=str(item.get("view", "front")),
        )
        for item in data["cameras"]
    ]


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _load_masks(image: bpy.types.Image) -> tuple[np.ndarray, np.ndarray]:
    """Máscara del objeto (alfa) y su versión erosionada, con el origen arriba a la izquierda."""
    w, h = image.size
    px = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)[::-1]  # Blender guarda las filas de abajo hacia arriba
    full = mask = px[..., 3] > 0.5
    for _ in range(ERODE_PX):  # erosión simple con vecinos en cruz
        m = mask.copy()
        m[1:] &= mask[:-1]
        m[:-1] &= mask[1:]
        m[:, 1:] &= mask[:, :-1]
        m[:, :-1] &= mask[:, 1:]
        mask = m
    return full, mask


@dataclass
class _Mesh:
    verts: np.ndarray
    vnormals: np.ndarray
    fnormals: np.ndarray
    fcenters: np.ndarray
    loop_vert: np.ndarray
    loop_face: np.ndarray
    bvh: BVHTree
    eps: float


def _mesh_data(obj: bpy.types.Object) -> _Mesh:
    mesh = obj.data
    nv, nf, nl = len(mesh.vertices), len(mesh.polygons), len(mesh.loops)

    def get(collection: object, attr: str, n: int, width: int, dtype: type = np.float64):
        arr = np.empty(n * width, dtype=dtype)
        collection.foreach_get(attr, arr)  # type: ignore[attr-defined]
        return arr.reshape(n, width) if width > 1 else arr

    mw = np.array(obj.matrix_world, dtype=np.float64)
    verts = get(mesh.vertices, "co", nv, 3) @ mw[:3, :3].T + mw[:3, 3]
    loop_face = np.repeat(np.arange(nf), [len(p.vertices) for p in mesh.polygons])
    return _Mesh(
        verts=verts,
        vnormals=get(mesh.vertices, "normal", nv, 3),
        fnormals=get(mesh.polygons, "normal", nf, 3),
        fcenters=get(mesh.polygons, "center", nf, 3),
        loop_vert=get(mesh.loops, "vertex_index", nl, 1, np.int64),
        loop_face=loop_face,
        bvh=BVHTree.FromObject(obj, bpy.context.evaluated_depsgraph_get()),
        eps=float(np.ptp(verts, axis=0).max() or 1.0) * 2e-3,
    )


def _camera_weights(
    m: _Mesh, cam: Camera, image: bpy.types.Image
) -> tuple[np.ndarray, np.ndarray, dict]:
    """UV por vértice en la imagen de `cam` y peso por esquina de cara."""
    full_mask, mask = _load_masks(image)
    res_y, res_x = mask.shape
    pos, right, up, back = cam.axes
    tan_half = math.tan(math.radians(cam.fov_deg) / 2.0)

    rel = m.verts - pos
    depth = -(rel @ back)
    u = 0.5 + (rel @ right) / (2.0 * tan_half * depth)
    v = 0.5 + (rel @ up) / (2.0 * tan_half * depth)

    px = np.clip((u * res_x).astype(int), 0, res_x - 1)
    py = np.clip(((1.0 - v) * res_y).astype(int), 0, res_y - 1)
    in_frame = (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1) & (depth > 0)
    alignment = float((full_mask[py, px] & in_frame).mean())
    info = {"view": cam.name, "alignment": round(alignment, 3), "used": False}
    if alignment < MIN_ALIGNMENT:
        info["reason"] = "la cámara no coincide con el modelo"
        return u, np.zeros(len(m.loop_vert), dtype=np.float32), info

    # Visibilidad: rayos solo desde vértices que miran hacia la cámara.
    to_cam = pos - m.verts
    to_cam /= np.linalg.norm(to_cam, axis=1, keepdims=True)
    candidates = np.flatnonzero((m.vnormals * to_cam).sum(axis=1) > MIN_FACING / 2)
    visible = np.zeros(len(m.verts), dtype=bool)
    origin = Vector(pos)
    for i in candidates:
        direction = Vector(m.verts[i]) - origin
        dist = direction.length
        hit, *_ = m.bvh.ray_cast(origin, direction / dist, dist - m.eps)
        visible[i] = hit is None

    face_dir = pos - m.fcenters
    face_dir /= np.linalg.norm(face_dir, axis=1, keepdims=True)
    facing = _smoothstep(MIN_FACING, FULL_FACING, (m.fnormals * face_dir).sum(axis=1))
    usable = (visible & mask[py, px] & in_frame)[m.loop_vert]
    weight = usable.astype(np.float32) * facing[m.loop_face].astype(np.float32)
    info.update(used=True, coverage=round(float((weight > 0.5).mean()), 3))
    return np.stack([u, v], axis=1), weight, info


def project_views(obj: bpy.types.Object, cameras: list[Camera]) -> dict[str, object]:
    """Proyecta cada cámara y hornea la mezcla sobre la textura de color de `obj`."""
    m = _mesh_data(obj)
    mesh = obj.data
    images, weights, layers, views = [], [], [], []
    for index, cam in enumerate(cameras):
        image = bpy.data.images.load(str(cam.image), check_existing=False)
        image.alpha_mode = "STRAIGHT"
        uv, weight, info = _camera_weights(m, cam, image)
        views.append(info)
        if not info["used"]:
            bpy.data.images.remove(image)
            continue
        layer = f"3dai_proj_{index}"
        uv_layer = mesh.uv_layers.new(name=layer)
        uv_layer.data.foreach_set("uv", uv[m.loop_vert].ravel())
        images.append(image)
        weights.append(weight)
        layers.append(layer)

    result: dict[str, object] = {"views": views, "applied": False}
    if not images:
        return result

    # Normalizar: donde varias cámaras ven lo mismo se reparten; el resto queda generado.
    stack = np.stack(weights)
    total = stack.sum(axis=0)
    stack = stack / np.maximum(total, 1.0)
    gen = 1.0 - np.minimum(total, 1.0)
    for layer, w in zip(layers, stack, strict=True):
        _write_weight(mesh, f"{layer}_w", w)
    _write_weight(mesh, GEN_WEIGHT, gen)

    result["applied"] = _bake(obj, images, layers)
    result["projected_fraction"] = round(float((total > 0.5).mean()), 3)
    for layer in layers:
        mesh.uv_layers.remove(mesh.uv_layers[layer])
        mesh.color_attributes.remove(mesh.color_attributes[f"{layer}_w"])
    mesh.color_attributes.remove(mesh.color_attributes[GEN_WEIGHT])
    for image in images:
        bpy.data.images.remove(image)
    return result


def _write_weight(mesh: bpy.types.Mesh, name: str, values: np.ndarray) -> None:
    attr = mesh.color_attributes.new(name, "FLOAT_COLOR", "CORNER")
    rgba = np.repeat(values.astype(np.float32)[:, None], 4, axis=1)
    rgba[:, 3] = 1.0
    attr.data.foreach_set("color", rgba.ravel())


def _color_texture_node(material: bpy.types.Material) -> bpy.types.Node | None:
    bsdf = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None or not bsdf.inputs["Base Color"].is_linked:
        return None
    node = bsdf.inputs["Base Color"].links[0].from_node
    return node if node.type == "TEX_IMAGE" and node.image is not None else None


def _bake(obj: bpy.types.Object, images: list[bpy.types.Image], layers: list[str]) -> bool:
    """Hornea generada x gen_w + suma(imagen_i x w_i) sobre la textura de color."""
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
        made: list[bpy.types.Node] = []

        def new(kind: str, tree: bpy.types.NodeTree = tree, made: list = made) -> bpy.types.Node:
            node = tree.nodes.new(kind)
            node.label = "3DAI proj"
            made.append(node)
            return node

        def scaled(
            color: bpy.types.NodeSocket,
            weight_attr: str,
            tree: bpy.types.NodeTree = tree,
            new: object = new,
        ) -> bpy.types.NodeSocket:
            weight = new("ShaderNodeAttribute")
            weight.attribute_name = weight_attr
            scale = new("ShaderNodeVectorMath")
            scale.operation = "MULTIPLY"
            tree.links.new(color, scale.inputs[0])
            tree.links.new(weight.outputs["Color"], scale.inputs[1])
            return scale.outputs["Vector"]

        acc = scaled(color_node.outputs["Color"], GEN_WEIGHT)
        for image, layer in zip(images, layers, strict=True):
            uv = new("ShaderNodeUVMap")
            uv.uv_map = layer
            tex = new("ShaderNodeTexImage")
            tex.image = image
            tex.extension = "CLIP"
            tree.links.new(uv.outputs["UV"], tex.inputs["Vector"])
            add = new("ShaderNodeVectorMath")
            add.operation = "ADD"
            tree.links.new(acc, add.inputs[0])
            tree.links.new(scaled(tex.outputs["Color"], f"{layer}_w"), add.inputs[1])
            acc = add.outputs["Vector"]

        emission = new("ShaderNodeEmission")
        output = new("ShaderNodeOutputMaterial")
        bake_node = new("ShaderNodeTexImage")
        bake_node.image = target
        tree.links.new(acc, emission.inputs["Color"])
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
