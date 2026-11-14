"""3DAI Modeler: extensión de Blender para generar modelos 3D con IA local.

`bpy` se importa solo en ui/ (y en worker/), para que `core/` se pueda usar y testear
fuera de Blender.
"""


def register() -> None:
    from . import ui

    ui.register()


def unregister() -> None:
    from . import ui

    ui.unregister()
