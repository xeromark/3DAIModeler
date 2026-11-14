"""Estilos visuales: cambian cómo se ve la escena en Blender, sin tocar los modelos.

- Postprocesado (compositor de la escena): blanco y negro, colores saturados, acuarela.
- Sombreado (materiales): anime / cel shading, con contorno por casco invertido.

Todo es reversible. Los nodos y modificadores que agrega el addon llevan el prefijo "3DAI";
en los materiales se agrega una salida alternativa y se activa, sin borrar la original.
"""

from __future__ import annotations

import itertools

import bpy

PREFIX = "3DAI"
COMPOSITOR_GROUP = "3DAI Estilo"
OUTLINE_MOD = "3DAI Contorno"
OUTLINE_MAT = "3DAI Contorno"
PREV_COMPOSITOR_KEY = "threedai_prev_compositor"
JOB_KEY = "threedai_job"
"""Propiedad que marca los objetos importados por el addon."""

STYLE_ITEMS = [
    ("NONE", "Original", "Sin estilo: los materiales y la imagen tal cual", "SHADING_RENDERED", 0),
    ("ANIME", "Anime / Cel shading", "Sombras en bandas y contorno negro", "SHADING_SOLID", 1),
    ("BW", "Blanco y negro", "Sin color, con contraste ajustable", "IMAGE_ZDEPTH", 2),
    ("SATURATED", "Colores saturados", "Colores más intensos y con más contraste", "COLOR", 3),
    ("WATERCOLOR", "Acuarela", "Pinceladas suaves y colores lavados", "BRUSH_DATA", 4),
]

COMPOSITOR_STYLES = {"BW", "SATURATED", "WATERCOLOR", "ANIME"}


# ---------------------------------------------------------------- objetivos


def target_objects(context: bpy.types.Context, apply_to: str) -> list[bpy.types.Object]:
    if apply_to == "SELECTED":
        objects = list(context.selected_objects)
    else:
        objects = [o for o in context.scene.objects if JOB_KEY in o]
    return [o for o in objects if o.type == "MESH"]


# --------------------------------------------------------------- compositor


def _new(tree: bpy.types.NodeTree, kind: str, x: float, **inputs: object) -> bpy.types.Node:
    node = tree.nodes.new(kind)
    node.location = (x, 0)
    for name, value in inputs.items():
        node.inputs[name.replace("_", " ")].default_value = value
    return node


def _chain(tree: bpy.types.NodeTree, nodes: list[bpy.types.Node]) -> None:
    for a, b in itertools.pairwise(nodes):
        tree.links.new(a.outputs["Image"], b.inputs["Image"])


def build_compositor(scene: bpy.types.Scene, style: str, intensity: float) -> None:
    """Grupo de compositor del estilo. Guarda el grupo previo del usuario para restaurarlo."""
    previous = scene.compositing_node_group
    if previous is not None and previous.name != COMPOSITOR_GROUP:
        scene[PREV_COMPOSITOR_KEY] = previous.name
    tree = bpy.data.node_groups.get(COMPOSITOR_GROUP)
    if tree is None:
        tree = bpy.data.node_groups.new(COMPOSITOR_GROUP, "CompositorNodeTree")
        tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    tree.nodes.clear()

    render = tree.nodes.new("CompositorNodeRLayers")
    render.location = (-600, 0)
    k = intensity
    if style == "BW":
        steps = [
            _new(tree, "CompositorNodeHueSat", -350, Saturation=0.0),
            _new(tree, "CompositorNodeBrightContrast", -150, Brightness=3.0, Contrast=12.0 * k),
        ]
    elif style == "SATURATED":
        steps = [
            _new(tree, "CompositorNodeHueSat", -350, Saturation=1.1 + 0.7 * k, Value=1.04),
            _new(tree, "CompositorNodeBrightContrast", -150, Contrast=2.0 + 8.0 * k),
        ]
    elif style == "WATERCOLOR":
        kuwahara = _new(tree, "CompositorNodeKuwahara", -400, Size=3.0 + 9.0 * k)
        kuwahara.inputs["Type"].default_value = "Anisotropic"
        steps = [
            kuwahara,
            _new(tree, "CompositorNodeHueSat", -200, Saturation=1.05, Value=1.12),
            _new(tree, "CompositorNodeBrightContrast", 0, Brightness=6.0, Contrast=-4.0 * k),
        ]
    else:  # ANIME: un poco más de color sobre el sombreado plano
        steps = [_new(tree, "CompositorNodeHueSat", -300, Saturation=1.15 + 0.3 * k)]
    output = tree.nodes.new("NodeGroupOutput")
    output.location = (250, 0)
    _chain(tree, [render, *steps])
    tree.links.new(steps[-1].outputs["Image"], output.inputs["Image"])
    scene.compositing_node_group = tree


def clear_compositor(scene: bpy.types.Scene) -> None:
    previous_name = scene.get(PREV_COMPOSITOR_KEY)
    previous = bpy.data.node_groups.get(previous_name) if previous_name else None
    scene.compositing_node_group = previous
    if PREV_COMPOSITOR_KEY in scene:
        del scene[PREV_COMPOSITOR_KEY]
    tree = bpy.data.node_groups.get(COMPOSITOR_GROUP)
    if tree is not None and tree.users == 0:
        bpy.data.node_groups.remove(tree)


# ------------------------------------------------------------------ toon


def _base_color_source(material: bpy.types.Material) -> bpy.types.NodeSocket | None:
    bsdf = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return None
    base = bsdf.inputs["Base Color"]
    return base.links[0].from_socket if base.is_linked else None


def _remove_toon_nodes(material: bpy.types.Material) -> None:
    """Quita los nodos del estilo y reactiva la salida original del material."""
    tree = material.node_tree
    for node in [n for n in tree.nodes if n.label.startswith(PREFIX)]:
        tree.nodes.remove(node)
    outputs = [n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"]
    original = tree.nodes.get(material.get("threedai_original_output", ""))
    target = original if original in outputs else (outputs[0] if outputs else None)
    if target is not None:
        target.is_active_output = True


def apply_toon(material: bpy.types.Material, bands: int, intensity: float) -> None:
    """Salida alternativa: luz -> bandas planas (rampa constante) por color base -> emisión."""
    if not material.use_nodes:
        return
    _remove_toon_nodes(material)
    tree = material.node_tree
    active = next(
        (n for n in tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None
    )
    if active is not None:
        material["threedai_original_output"] = active.name

    def node(kind: str, x: float, y: float = -600) -> bpy.types.Node:
        n = tree.nodes.new(kind)
        n.label = f"{PREFIX} cel"
        n.location = (x, y)
        return n

    diffuse = node("ShaderNodeBsdfDiffuse", 0)
    to_rgb = node("ShaderNodeShaderToRGB", 200)
    ramp = node("ShaderNodeValToRGB", 400)
    ramp.color_ramp.interpolation = "CONSTANT"
    elements = ramp.color_ramp.elements
    shadow = 0.6 - 0.35 * intensity  # más intensidad = sombras más oscuras
    while len(elements) < bands:
        elements.new(0.5)
    for i, element in enumerate(elements):
        element.position = i / bands
        level = shadow + (1.0 - shadow) * i / max(1, bands - 1)
        element.color = (level, level, level, 1.0)
    multiply = node("ShaderNodeMix", 650)
    multiply.data_type = "RGBA"
    multiply.blend_type = "MULTIPLY"
    multiply.inputs["Factor"].default_value = 1.0
    emission = node("ShaderNodeEmission", 850)
    output = node("ShaderNodeOutputMaterial", 1050)

    tree.links.new(diffuse.outputs["BSDF"], to_rgb.inputs["Shader"])
    tree.links.new(to_rgb.outputs["Color"], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], multiply.inputs["A"])
    base = _base_color_source(material)
    if base is not None:
        tree.links.new(base, multiply.inputs["B"])
    tree.links.new(multiply.outputs["Result"], emission.inputs["Color"])
    tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    output.is_active_output = True


def remove_toon(material: bpy.types.Material) -> None:
    if material.use_nodes:
        _remove_toon_nodes(material)
    if "threedai_original_output" in material:
        del material["threedai_original_output"]


def _outline_material() -> bpy.types.Material:
    mat = bpy.data.materials.get(OUTLINE_MAT)
    if mat is None:
        mat = bpy.data.materials.new(OUTLINE_MAT)
        mat.use_nodes = True
        tree = mat.node_tree
        tree.nodes.clear()
        emission = tree.nodes.new("ShaderNodeEmission")
        emission.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
        out = tree.nodes.new("ShaderNodeOutputMaterial")
        tree.links.new(emission.outputs["Emission"], out.inputs["Surface"])
        mat.diffuse_color = (0.0, 0.0, 0.0, 1.0)
    mat.use_backface_culling = True
    return mat


def apply_outline(obj: bpy.types.Object, thickness: float) -> None:
    """Contorno por casco invertido: Solidify con normales invertidas y material negro."""
    remove_outline(obj)
    outline = _outline_material()
    obj.data.materials.append(outline)
    mod = obj.modifiers.new(OUTLINE_MOD, "SOLIDIFY")
    mod.thickness = -thickness * max(obj.dimensions)
    mod.use_flip_normals = True
    mod.use_rim = False
    mod.material_offset = len(obj.data.materials) - 1
    for slot in obj.material_slots:
        if slot.material is not None and slot.material is not outline:
            slot.material.use_backface_culling = False


def remove_outline(obj: bpy.types.Object) -> None:
    mod = obj.modifiers.get(OUTLINE_MOD)
    if mod is not None:
        obj.modifiers.remove(mod)
    mats = obj.data.materials
    for i in reversed(range(len(mats))):
        if mats[i] is not None and mats[i].name == OUTLINE_MAT:
            mats.pop(index=i)


# --------------------------------------------------------------- aplicar


def _set_viewports(context: bpy.types.Context, compositor: bool) -> None:
    """Muestra el estilo en los viewports (vista de material + compositor)."""
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            shading = area.spaces.active.shading
            if shading.type in ("SOLID", "WIREFRAME"):
                shading.type = "MATERIAL"
            shading.use_compositor = "ALWAYS" if compositor else "DISABLED"


def apply_style(context: bpy.types.Context) -> int:
    """Aplica el estilo elegido en la escena. Devuelve cuántos objetos se tocaron."""
    props = context.scene.threedai
    style = props.style
    objects = target_objects(context, props.style_apply_to)
    materials = {s.material for o in objects for s in o.material_slots if s.material}
    materials.discard(bpy.data.materials.get(OUTLINE_MAT))

    for obj in objects:
        remove_outline(obj)
    for material in materials:
        remove_toon(material)

    if style == "NONE":
        clear_compositor(context.scene)
        _set_viewports(context, compositor=False)
        return len(objects)

    if style == "ANIME":
        for material in materials:
            apply_toon(material, props.cel_bands, props.style_intensity)
        if props.cel_outline:
            for obj in objects:
                apply_outline(obj, props.outline_thickness)
    if style in COMPOSITOR_STYLES:
        build_compositor(context.scene, style, props.style_intensity)
    _set_viewports(context, compositor=style in COMPOSITOR_STYLES)
    return len(objects)
