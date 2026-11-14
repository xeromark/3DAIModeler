"""Presets de calidad y de polígonos (models/presets.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


class PresetError(ValueError):
    """Preset desconocido o inválido."""


@dataclass(frozen=True)
class Quality:
    name: str
    description: str
    structure_steps: int
    shape_steps: int
    upsample_steps: int
    texture_steps: int
    upsample_resolution: int
    remesh_resolution: int
    texture_size: int
    normal_size: int
    ao_size: int

    def __post_init__(self) -> None:
        if self.upsample_resolution not in (1024, 1536, 2048):
            raise PresetError(f"{self.name}: upsample_resolution debe ser 1024, 1536 o 2048")
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, int) and value <= 0:
                raise PresetError(f"{self.name}: {f.name} debe ser > 0")


@dataclass(frozen=True)
class Presets:
    quality: dict[str, Quality]
    polycount: dict[str, int]

    @classmethod
    def load(cls, path: Path) -> Presets:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        if data.get("schema") != 1:
            raise PresetError(f"schema no soportado: {data.get('schema')!r}")
        quality = {
            name: Quality(name=name, **values) for name, values in data.get("quality", {}).items()
        }
        polycount = {k: int(v) for k, v in data.get("polycount", {}).items()}
        return cls(quality, polycount)

    def get_quality(self, name: str) -> Quality:
        try:
            return self.quality[name]
        except KeyError:
            options = list(self.quality)
            raise PresetError(f"calidad desconocida: {name} (opciones: {options})") from None

    def resolve_faces(self, value: str | int) -> int:
        """Acepta un preset ('medium') o un número de caras ('35000' o 35000)."""
        if isinstance(value, int) or str(value).replace("_", "").isdigit():
            faces = int(str(value).replace("_", ""))
        elif value in self.polycount:
            faces = self.polycount[value]
        else:
            raise PresetError(f"polígonos desconocidos: {value} (opciones: {list(self.polycount)})")
        if not 100 <= faces <= 5_000_000:
            raise PresetError("las caras deben estar entre 100 y 5.000.000")
        return faces
