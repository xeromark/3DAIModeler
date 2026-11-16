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


class ThreeDAISaveProjection:
    """Guarda la imagen recortada que vio Pixal3D y el FOV de su cámara.

    Con eso Blender reconstruye la misma cámara y proyecta la imagen original sobre el modelo
    (texturas con la nitidez de la imagen en lo visible desde ese punto de vista).
    """

    CATEGORY = "3DAI"
    RETURN_TYPES = ()
    FUNCTION = "run"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "fov": ("FLOAT", {"default": 49.13, "min": 1.0, "max": 170.0}),
                "filename_prefix": ("STRING", {"default": "threedai/proj"}),
            },
            "optional": {"mask": ("IMAGE",)},
        }

    def run(self, image, fov, filename_prefix, mask=None):
        from pathlib import Path

        import folder_paths
        import numpy as np
        from PIL import Image

        out_dir, name, counter, subfolder, _ = folder_paths.get_save_image_path(
            filename_prefix, folder_paths.get_output_directory(), image.shape[2], image.shape[1]
        )
        rgb = image[0, ..., :3].cpu().numpy()
        if mask is not None:  # alfa = máscara del objeto, recortada igual que la imagen
            alpha = mask[0, ..., :1].cpu().numpy()
            rgb = np.concatenate([rgb, alpha], axis=-1)
        pixels = (rgb * 255.0).clip(0, 255).astype(np.uint8)
        filename = f"{name}_{counter:05}_.png"
        Image.fromarray(pixels).save(Path(out_dir) / filename)
        saved = {"filename": filename, "subfolder": subfolder, "type": "output"}
        return {"ui": {"images": [saved], "fov": [float(fov)]}}


NODE_CLASS_MAPPINGS = {
    "ThreeDAIFreeVRAM": ThreeDAIFreeVRAM,
    "ThreeDAISaveProjection": ThreeDAISaveProjection,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ThreeDAIFreeVRAM": "3DAI: liberar VRAM antes de hornear",
    "ThreeDAISaveProjection": "3DAI: guardar imagen y cámara para proyectar",
}
