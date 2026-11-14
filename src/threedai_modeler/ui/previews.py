"""Miniaturas de imágenes (entrada del panel Generar y trabajos en Resultados)."""

from __future__ import annotations

from pathlib import Path

import bpy.utils.previews

_collection: bpy.utils.previews.ImagePreviewCollection | None = None


def icon(path: Path) -> int:
    """icon_id de la miniatura de `path` (0 si no existe)."""
    global _collection
    if not path.is_file():
        return 0
    if _collection is None:
        _collection = bpy.utils.previews.new()
    key = str(path)
    if key not in _collection:
        _collection.load(key, key, "IMAGE")
    return int(_collection[key].icon_id)


def clear() -> None:
    global _collection
    if _collection is not None:
        bpy.utils.previews.remove(_collection)
        _collection = None
