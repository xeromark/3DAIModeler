"""3DAI Modeler: extensión de Blender para generar modelos 3D con IA local.

`bpy` se importa solo dentro de register/unregister y en ui/ y ops/, para que
`core/` se pueda usar y testear fuera de Blender.
"""


def register() -> None:
    """Registra la extensión en Blender. Los paneles llegan en M3."""


def unregister() -> None:
    """Quita la extensión de Blender."""
