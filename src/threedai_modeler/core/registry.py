"""Catálogo de modelos: lectura, estado en disco, descarga verificada y borrado seguro."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from .paths import Workspace

HF_BASE_URL = "https://huggingface.co"
USER_AGENT = "threedai-modeler"
CHUNK = 4 * 1024 * 1024

APPROVED_ORGS = frozenset({"Comfy-Org", "microsoft", "tencent", "TencentARC", "Tencent-Hunyuan"})
"""Organizaciones de Hugging Face aceptadas sin advertencia."""

SAFE_SUFFIXES = (".safetensors",)
"""Formatos que no ejecutan código al cargarse."""

ProgressFn = Callable[[int, int], None]
"""Recibe (bytes descargados, bytes totales)."""


class RegistryError(ValueError):
    """El catálogo es inválido."""


class DownloadError(RuntimeError):
    """Falló una descarga o su verificación."""


class DownloadCancelledError(DownloadError):
    """El usuario canceló la descarga."""


class ModelState(StrEnum):
    MISSING = "missing"
    PARTIAL = "partial"
    INSTALLED = "installed"


@dataclass(frozen=True)
class FileSpec:
    id: str
    repo: str
    revision: str
    path: str
    dest: str
    size: int
    sha256: str

    @property
    def filename(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def url(self) -> str:
        return f"{HF_BASE_URL}/{self.repo}/resolve/{self.revision}/{self.path}"

    @property
    def org(self) -> str:
        return self.repo.split("/", 1)[0]

    @property
    def is_safe_format(self) -> bool:
        return self.filename.endswith(SAFE_SUFFIXES)


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    family: str
    roles: tuple[str, ...]
    precision: str
    license: str
    files: tuple[str, ...]
    default: bool = False
    user_added: bool = False


@dataclass
class Registry:
    files: dict[str, FileSpec] = field(default_factory=dict)
    models: dict[str, ModelSpec] = field(default_factory=dict)
    user_files: set[str] = field(default_factory=set)
    """IDs de archivos que vienen del registro del usuario."""

    # ------------------------------------------------------------------ carga

    @classmethod
    def load(cls, official: Path, user: Path | None = None) -> Registry:
        reg = cls()
        reg._merge(_read_toml(official), user_added=False)
        if user is not None and user.is_file():
            reg._merge(_read_toml(user), user_added=True)
        reg.validate()
        return reg

    @classmethod
    def for_workspace(cls, ws: Workspace) -> Registry:
        return cls.load(ws.registry_file, ws.user_registry_file)

    def _merge(self, data: dict[str, Any], *, user_added: bool) -> None:
        if data.get("schema") != 1:
            raise RegistryError(f"schema no soportado: {data.get('schema')!r}")
        for fid, raw in data.get("files", {}).items():
            if fid in self.files:
                raise RegistryError(f"archivo duplicado: {fid}")
            self.files[fid] = _parse_file(fid, raw)
            if user_added:
                self.user_files.add(fid)
        for raw in data.get("models", []):
            model = _parse_model(raw, user_added=user_added)
            if model.id in self.models:
                raise RegistryError(f"modelo duplicado: {model.id}")
            self.models[model.id] = model

    def validate(self) -> None:
        for model in self.models.values():
            unknown = [f for f in model.files if f not in self.files]
            if unknown:
                raise RegistryError(f"{model.id}: archivos desconocidos {unknown}")
        defaults = [m.id for m in self.models.values() if m.default]
        if len(defaults) > 1:
            raise RegistryError(f"más de un modelo por defecto: {defaults}")

    # ------------------------------------------------------------- consultas

    def model(self, model_id: str) -> ModelSpec:
        try:
            return self.models[model_id]
        except KeyError:
            raise RegistryError(f"modelo desconocido: {model_id}") from None

    def default_model(self) -> ModelSpec | None:
        return next((m for m in self.models.values() if m.default), None)

    def files_of(self, model_id: str) -> list[FileSpec]:
        return [self.files[f] for f in self.model(model_id).files]

    def model_size(self, model_id: str) -> int:
        return sum(f.size for f in self.files_of(model_id))

    def local_path(self, ws: Workspace, spec: FileSpec) -> Path:
        return ws.comfy_models_dir / spec.dest / spec.filename

    def is_file_installed(self, ws: Workspace, spec: FileSpec) -> bool:
        path = self.local_path(ws, spec)
        return path.is_file() and path.stat().st_size == spec.size

    def state(self, ws: Workspace, model_id: str) -> ModelState:
        present = [self.is_file_installed(ws, f) for f in self.files_of(model_id)]
        if all(present):
            return ModelState.INSTALLED
        return ModelState.PARTIAL if any(present) else ModelState.MISSING

    def missing_bytes(self, ws: Workspace, model_id: str) -> int:
        return sum(f.size for f in self.files_of(model_id) if not self.is_file_installed(ws, f))

    def installed_models(self, ws: Workspace) -> list[ModelSpec]:
        return [m for m in self.models.values() if self.state(ws, m.id) is ModelState.INSTALLED]

    def removable_files(self, ws: Workspace, model_id: str) -> list[FileSpec]:
        """Archivos del modelo que ningún otro modelo instalado necesita."""
        keep: set[str] = set()
        for other in self.installed_models(ws):
            if other.id != model_id:
                keep.update(other.files)
        return [f for f in self.files_of(model_id) if f.id not in keep]

    # -------------------------------------------------------------- acciones

    def download_model(
        self,
        ws: Workspace,
        model_id: str,
        progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        """Descarga los archivos que falten. `progress` recibe el avance total del modelo."""
        pending = [f for f in self.files_of(model_id) if not self.is_file_installed(ws, f)]
        total = sum(f.size for f in pending)
        done = 0
        for spec in pending:

            def on_file(n: int, _total: int, base: int = done) -> None:
                if progress:
                    progress(base + n, total)

            download_file(spec, self.local_path(ws, spec), on_file, cancel)
            done += spec.size

    def remove_model(self, ws: Workspace, model_id: str) -> list[Path]:
        """Borra los archivos del modelo que no comparte con otros modelos instalados."""
        removed = []
        for spec in self.removable_files(ws, model_id):
            path = self.local_path(ws, spec)
            for p in (path, _part_path(path)):
                if p.exists():
                    p.unlink()
                    removed.append(p)
        return removed

    # ------------------------------------------------ variantes del usuario

    def make_variant(
        self,
        weights: FileSpec,
        *,
        family: str,
        name: str,
        precision: str,
        license: str = "desconocida",
    ) -> ModelSpec:
        """Modelo nuevo de una familia soportada: sus pesos + los compartidos de la familia.

        Los compartidos (VAE, codificador de imagen, quitar fondo...) se toman del modelo oficial
        de la misma familia, todos menos sus pesos de difusión.
        """
        official = (m for m in self.models.values() if m.family == family and not m.user_added)
        base = next(official, None)
        if base is None:
            raise RegistryError(f"familia no soportada: {family}")
        shared = [f for f in base.files if self.files[f].dest != weights.dest]
        model_id = _slug(f"{family}-{name}")
        if model_id in self.models:
            raise RegistryError(f"ya existe un modelo con id {model_id}")
        return ModelSpec(
            id=model_id,
            name=name,
            family=family,
            roles=base.roles,
            precision=precision,
            license=license,
            files=(weights.id, *shared),
            user_added=True,
        )

    def add_user_variant(self, ws: Workspace, weights: FileSpec, model: ModelSpec) -> None:
        if weights.id not in self.files:
            self.files[weights.id] = weights
            self.user_files.add(weights.id)
        self.models[model.id] = model
        self.validate()
        self.save_user(ws.user_registry_file)

    def remove_user_variant(self, ws: Workspace, model_id: str) -> list[Path]:
        model = self.model(model_id)
        if not model.user_added:
            raise RegistryError(f"{model_id} es oficial; solo se puede borrar su descarga")
        removed = self.remove_model(ws, model_id)
        del self.models[model_id]
        used = {f for m in self.models.values() for f in m.files}
        for fid in list(self.user_files):
            if fid not in used:
                self.user_files.discard(fid)
                del self.files[fid]
        self.save_user(ws.user_registry_file)
        return removed

    def save_user(self, path: Path) -> None:
        """Escribe el registro del usuario (solo sus archivos y modelos)."""
        lines = ["# Modelos agregados desde el addon.", "", "schema = 1", ""]
        for fid in sorted(self.user_files):
            f = self.files[fid]
            lines += [
                f"[files.{fid}]",
                f"repo = {_toml_str(f.repo)}",
                f"revision = {_toml_str(f.revision)}",
                f"path = {_toml_str(f.path)}",
                f"dest = {_toml_str(f.dest)}",
                f"size = {f.size}",
                f"sha256 = {_toml_str(f.sha256)}",
                "",
            ]
        for m in self.models.values():
            if not m.user_added:
                continue
            lines += [
                "[[models]]",
                f"id = {_toml_str(m.id)}",
                f"name = {_toml_str(m.name)}",
                f"family = {_toml_str(m.family)}",
                f"roles = {json.dumps(list(m.roles))}",
                f"precision = {_toml_str(m.precision)}",
                f"license = {_toml_str(m.license)}",
                f"files = {json.dumps(list(m.files))}",
                "",
            ]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines))
        tmp.replace(path)


# ---------------------------------------------------------- Hugging Face


def fetch_hf_file(
    repo: str,
    path: str,
    revision: str = "main",
    dest: str = "diffusion_models",
    timeout: float = 30,
) -> FileSpec:
    """Consulta Hugging Face y devuelve el FileSpec con la revisión fijada a un commit."""
    if not re.fullmatch(r"[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*", repo) or ".." in repo:
        raise RegistryError(f"repositorio inválido: {repo!r} (formato: organizacion/nombre)")
    if ".." in PurePosixPath(path).parts or path.startswith("/"):
        raise RegistryError(f"ruta de archivo inválida: {path!r}")
    api = (
        f"{HF_BASE_URL}/api/models/{repo}/revision/{urllib.parse.quote(revision, safe='')}"
        "?blobs=true"
    )
    request = urllib.request.Request(api, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            info = json.load(resp)
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise RegistryError(f"{repo} es privado o pide aceptar su licencia en la web") from err
        if err.code == 404:
            raise RegistryError(f"no existe {repo} (o la revisión {revision})") from err
        raise RegistryError(f"Hugging Face respondió HTTP {err.code}") from err
    except (urllib.error.URLError, OSError) as err:
        raise RegistryError(f"sin conexión con Hugging Face: {err}") from err
    if info.get("gated"):
        raise RegistryError(f"{repo} requiere aceptar su licencia en la web (gated)")
    sibling = next((s for s in info.get("siblings", []) if s.get("rfilename") == path), None)
    if sibling is None:
        raise RegistryError(f"{repo} no tiene el archivo {path}")
    lfs = sibling.get("lfs") or {}
    if not lfs.get("sha256"):
        raise RegistryError(f"{path} no tiene hash SHA-256 publicado (no es un archivo LFS)")
    raw = {
        "repo": repo,
        "revision": info["sha"],
        "path": path,
        "dest": dest,
        "size": lfs.get("size") or sibling.get("size"),
        "sha256": lfs["sha256"],
    }
    return _parse_file(_slug(f"user-{repo}-{PurePosixPath(path).stem}"), raw)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------- descargas


def _part_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".part")


def _hash_existing(path: Path) -> tuple[Any, int]:
    digest = hashlib.sha256()
    size = 0
    if path.exists():
        with path.open("rb") as fh:
            while chunk := fh.read(CHUNK):
                digest.update(chunk)
                size += len(chunk)
    return digest, size


def download_file(
    spec: FileSpec,
    dest: Path,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    *,
    url: str | None = None,
    timeout: float = 60.0,
) -> None:
    """Descarga `spec` en `dest` reanudando un `.part` previo. Verifica tamaño y SHA-256.

    El archivo final solo aparece si la verificación pasa (renombrado atómico).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = _part_path(dest)
    digest, have = _hash_existing(part)
    if have > spec.size:
        part.unlink()
        digest, have = _hash_existing(part)

    if have < spec.size:
        request = urllib.request.Request(url or spec.url, headers={"User-Agent": USER_AGENT})
        if have:
            request.add_header("Range", f"bytes={have}-")
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            if have and resp.status != 206:
                # El servidor ignoró el Range: empezar de cero.
                digest, have = hashlib.sha256(), 0
                part.unlink()
            with part.open("ab") as out:
                while chunk := resp.read(CHUNK):
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelledError(spec.id)
                    out.write(chunk)
                    digest.update(chunk)
                    have += len(chunk)
                    if progress:
                        progress(have, spec.size)

    if have != spec.size:
        raise DownloadError(f"{spec.id}: tamaño {have} != {spec.size}")
    if digest.hexdigest() != spec.sha256:
        part.unlink()
        raise DownloadError(f"{spec.id}: SHA-256 no coincide, archivo descartado")
    part.replace(dest)


# ------------------------------------------------------------------ parseo


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _require(raw: dict[str, Any], key: str, where: str) -> Any:
    if key not in raw:
        raise RegistryError(f"{where}: falta '{key}'")
    return raw[key]


def _parse_file(fid: str, raw: dict[str, Any]) -> FileSpec:
    where = f"files.{fid}"
    spec = FileSpec(
        id=fid,
        repo=str(_require(raw, "repo", where)),
        revision=str(_require(raw, "revision", where)),
        path=str(_require(raw, "path", where)),
        dest=str(_require(raw, "dest", where)),
        size=int(_require(raw, "size", where)),
        sha256=str(_require(raw, "sha256", where)).lower(),
    )
    if len(spec.sha256) != 64:
        raise RegistryError(f"{where}: sha256 inválido")
    if ".." in PurePosixPath(spec.dest).parts or PurePosixPath(spec.dest).is_absolute():
        raise RegistryError(f"{where}: dest debe ser una carpeta relativa")
    if not spec.is_safe_format and not raw.get("allow_unsafe_format", False):
        raise RegistryError(
            f"{where}: {spec.filename} no es .safetensors; "
            "requiere allow_unsafe_format = true (puede ejecutar código al cargarse)"
        )
    return spec


def _parse_model(raw: dict[str, Any], *, user_added: bool) -> ModelSpec:
    mid = str(_require(raw, "id", "models"))
    where = f"models.{mid}"
    return ModelSpec(
        id=mid,
        name=str(_require(raw, "name", where)),
        family=str(_require(raw, "family", where)),
        roles=tuple(_require(raw, "roles", where)),
        precision=str(raw.get("precision", "")),
        license=str(raw.get("license", "desconocida")),
        files=tuple(_require(raw, "files", where)),
        default=bool(raw.get("default", False)),
        user_added=user_added,
    )


def unapproved_orgs(files: Iterable[FileSpec]) -> set[str]:
    """Organizaciones fuera de la lista aprobada (el addon pedirá confirmación)."""
    return {f.org for f in files if f.org not in APPROVED_ORGS}
