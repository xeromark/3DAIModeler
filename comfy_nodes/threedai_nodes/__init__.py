"""Nodos propios de 3DAI Modeler para ComfyUI.

ThreeDAIFreeVRAM: se coloca entre la generación (IA) y el horneado de texturas. Descarga todos
los modelos de la VRAM (pesos de Pixal3D/TRELLIS.2, VAE, codificadores) para que el horneado
tenga la GPU completa. Deja pasar la malla y los colores ya generados sin tocarlos. Los modelos
se vuelven a cargar solos en la siguiente generación.
"""

import comfy.model_management as mm


class ThreeDAIFreeVRAM:
    CATEGORY = "3DAI"
    RETURN_TYPES = ("MESH", "VOXEL")
    RETURN_NAMES = ("mesh", "voxel")
    FUNCTION = "run"
    DESCRIPTION = "Descarga los modelos de la VRAM antes del horneado (deja pasar malla y vóxeles)."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"mesh": ("MESH",), "voxel": ("VOXEL",)}}

    def run(self, mesh, voxel):
        mm.unload_all_models()
        mm.soft_empty_cache(True)
        return (mesh, voxel)


NODE_CLASS_MAPPINGS = {"ThreeDAIFreeVRAM": ThreeDAIFreeVRAM}
NODE_DISPLAY_NAME_MAPPINGS = {"ThreeDAIFreeVRAM": "3DAI: liberar VRAM antes de hornear"}
