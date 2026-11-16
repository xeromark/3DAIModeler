"""CLI para usar el núcleo sin Blender.

python -m threedai_modeler.cli models list
python -m threedai_modeler.cli models download pixal3d-int8
python -m threedai_modeler.cli models remove pixal3d-int8
python -m threedai_modeler.cli generate foto.png --quality standard --faces medium
python -m threedai_modeler.cli resume 20261114-153012-ab12
python -m threedai_modeler.cli jobs
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
from pathlib import Path

from .core.blender import BlenderNotFoundError, WorkerError, find_blender, run_worker
from .core.comfy import ComfyClient, ComfyError
from .core.engine import DockerRunner, EngineError
from .core.jobs import Job
from .core.paths import Workspace
from .core.pipeline import Pipeline, PipelineError, Progress
from .core.presets import CUSTOM, Presets, custom_quality, quality_values
from .core.registry import Registry
from .core.workflows import EXTRA_VIEWS, MULTIVIEW_FAMILY


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def cmd_models_list(ws: Workspace, reg: Registry, _args: argparse.Namespace) -> int:
    print(f"{'ID':26} {'ESTADO':10} {'TAMAÑO':>8}  {'LICENCIA':8} NOMBRE")
    for m in reg.models.values():
        mark = "*" if m.default else " "
        state = reg.state(ws, m.id).value
        print(f"{m.id:25}{mark} {state:10} {_gb(reg.model_size(m.id)):>8}  {m.license:8} {m.name}")
    return 0


def cmd_models_download(ws: Workspace, reg: Registry, args: argparse.Namespace) -> int:
    missing = reg.missing_bytes(ws, args.model)
    if not missing:
        print(f"{args.model}: ya instalado")
        return 0
    print(f"{args.model}: descargando {_gb(missing)}")
    last = 0.0

    def progress(done: int, total: int) -> None:
        nonlocal last
        now = time.monotonic()
        if now - last >= 2 or done == total:
            last = now
            print(f"  {done / total:6.1%}  {_gb(done)} / {_gb(total)}", flush=True)

    reg.download_model(ws, args.model, progress)
    print(f"{args.model}: instalado y verificado (SHA-256)")
    return 0


def cmd_models_remove(ws: Workspace, reg: Registry, args: argparse.Namespace) -> int:
    removed = reg.remove_model(ws, args.model)
    for path in removed:
        print(f"  borrado {path.relative_to(ws.root)}")
    print(f"{args.model}: {len(removed)} archivo(s) borrado(s)")
    return 0


def _run_job(ws: Workspace, reg: Registry, job: Job) -> int:
    pipeline = Pipeline(
        ws, reg, Presets.load(ws.presets_file), ComfyClient(), runner=DockerRunner(ws)
    )
    last = {"label": "", "pct": -1}

    def progress(p: Progress) -> None:
        pct = -1 if p.fraction is None else int(p.fraction * 100)
        if p.label != last["label"] or (pct >= 0 and pct >= last["pct"] + 10):
            suffix = f" {pct:3d}%" if pct >= 0 else ""
            print(f"  [{time.strftime('%H:%M:%S')}] {p.label}{suffix}", flush=True)
            last["label"], last["pct"] = p.label, pct

    cancel = threading.Event()
    faces = f"{job.target_faces} caras" if job.target_faces else "sin reducir"
    print(f"Trabajo {job.id} ({job.model_id}, {job.quality}, {faces})")
    try:
        pipeline.run(job, progress, cancel)
    except KeyboardInterrupt:
        cancel.set()
        print("Cancelado. Reanudar con: resume", job.id)
        return 130
    except (PipelineError, ComfyError, BlenderNotFoundError, WorkerError, EngineError) as err:
        print(f"Error: {err}", file=sys.stderr)
        print(f"Reanudar con: resume {job.id}", file=sys.stderr)
        return 1
    print(f"Listo: {job.output_path('postprocess', 'blend')}")
    metrics = {name: rec.metrics for name, rec in job.stages.items()}
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    post = job.stages["postprocess"].metrics
    if post and not post.get("faces_within_tolerance", True):
        print(
            f"Aviso: {post['faces']} caras (objetivo {post['target_faces']}): "
            "la malla original tenía menos detalle que el objetivo.",
            file=sys.stderr,
        )
    return 0


def cmd_generate(ws: Workspace, reg: Registry, args: argparse.Namespace) -> int:
    presets = Presets.load(ws.presets_file)
    custom: dict[str, int] = {}
    if args.quality == CUSTOM:
        base = quality_values(presets.get_quality(args.base))
        overrides = dict(item.split("=", 1) for item in args.set)
        custom = {**base, **{k: int(v) for k, v in overrides.items()}}
        custom_quality(custom)  # valida
    else:
        presets.get_quality(args.quality)
    views = {v: Path(getattr(args, v)) for v in EXTRA_VIEWS if getattr(args, v)}
    default = reg.default_model()
    if views and not args.model:  # varias vistas: modelo multivista
        default = next(iter(reg.models_of_family(MULTIVIEW_FAMILY)), None)
    model_id = args.model or (default.id if default else None)
    if model_id is None:
        print("Error: no hay modelo por defecto; usa --model", file=sys.stderr)
        return 2
    reg.model(model_id)
    job = Job.create(
        ws,
        Path(args.image),
        model_id=model_id,
        quality=args.quality,
        target_faces=presets.resolve_faces(args.faces),
        seed=args.seed if args.seed is not None else random.randrange(2**31),
        name=args.name,
        parts=args.parts,
        parts_detail=args.parts_detail,
        upright=not args.no_upright,
        views=views,
        custom_quality=custom,
        exports=[f for f in args.export.split(",") if f],
        project_texture=not args.no_project,
    )
    return _run_job(ws, reg, job)


def cmd_resume(ws: Workspace, reg: Registry, args: argparse.Namespace) -> int:
    job = Job.load(ws, args.job)
    if args.from_stage:
        job.reset_from(args.from_stage)
    return _run_job(ws, reg, job)


def cmd_jobs(ws: Workspace, _reg: Registry, _args: argparse.Namespace) -> int:
    for job in Job.list_all(ws):
        states = " ".join(f"{n}={r.status.value}" for n, r in job.stages.items())
        faces = f"{job.target_faces:>8} caras" if job.target_faces else " sin reducir"
        print(f"{job.id}  {job.model_id:16} {job.quality:9} {faces}  {states}")
    return 0


def cmd_preview(ws: Workspace, _reg: Registry, args: argparse.Namespace) -> int:
    job = Job.load(ws, args.job)
    glb = job.output_path("generate", "glb")
    if glb is None or not glb.is_file():
        print(f"Error: el trabajo {job.id} no tiene modelo generado", file=sys.stderr)
        return 1
    out = job.dir / "preview.png"
    script = ws.worker_dir / "render_preview.py"
    try:
        result = run_worker(find_blender(), script, [str(glb), str(out), str(args.size)])
    except (BlenderNotFoundError, WorkerError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    print(next((ln for ln in result.stdout.splitlines() if ln.startswith("PREVIEW")), out))
    return 0


def _render(ws: Workspace, src: Path, out: Path, color_parts: bool = False) -> None:
    args = [str(src), str(out), "512"] + (["--color-parts"] if color_parts else [])
    run_worker(find_blender(), ws.worker_dir / "render_preview.py", args, timeout=600)


def cmd_bench(ws: Workspace, reg: Registry, args: argparse.Namespace) -> int:
    """Genera cada imagen con los mismos parámetros y escribe un informe comparable."""
    presets = Presets.load(ws.presets_file)
    faces = presets.resolve_faces(args.faces)
    model_id = args.model or (reg.default_model() or reg.model("pixal3d-int8")).id
    date = time.strftime("%Y-%m-%d")
    out_dir = ws.engine_dir / "bench" / f"{date}-{args.label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for image in map(Path, args.images):
        print(f"\n=== {image.name}")
        job = Job.create(
            ws,
            image,
            model_id=model_id,
            quality=args.quality,
            target_faces=faces,
            seed=args.seed,
            name=image.stem,
            parts=args.parts,
        )
        code = _run_job(ws, reg, job)
        row: dict[str, object] = {"image": image.name, "job": job.id, "ok": code == 0}
        if code == 0:
            gen = job.stages["generate"].metrics
            parts = job.stages["parts"].metrics
            post = job.stages["postprocess"].metrics
            row.update(
                seconds=gen.get("seconds"),
                parts_seconds=parts.get("seconds", 0),
                peak_ram=gen.get("peak_ram_gib"),
                peak_vram=max(gen.get("peak_vram_gib", 0), parts.get("peak_vram_gib", 0)),
                faces=post.get("faces"),
                objects=len(post.get("objects", [])),
            )
            blend = job.output_path("postprocess", "blend")
            glb = job.output_path("generate", "glb")
            try:
                if glb is not None:
                    _render(ws, glb, out_dir / f"{image.stem}.png")
                if args.parts and blend is not None:
                    _render(ws, blend, out_dir / f"{image.stem}_partes.png", color_parts=True)
            except (BlenderNotFoundError, WorkerError) as err:
                print(f"Aviso: no se pudo renderizar: {err}", file=sys.stderr)
        rows.append(row)

    lines = [
        f"# Benchmark {date} — {args.label}",
        "",
        f"Modelo `{model_id}` · calidad `{args.quality}` · {faces} caras · semilla {args.seed}"
        + (" · con partes" if args.parts else ""),
        "",
        "| Imagen | Generar | Partes | Pico RAM* | Pico VRAM | Caras | Objetos |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if not r["ok"]:
            lines.append(f"| {r['image']} | falló (trabajo {r['job']}) | | | | | |")
            continue
        lines.append(
            f"| {r['image']} | {r['seconds']} s | {r['parts_seconds']} s | {r['peak_ram']} GiB "
            f"| {r['peak_vram']} GiB | {r['faces']} | {r['objects']} |"
        )
    previews = out_dir.relative_to(ws.root)
    lines += ["", "\\* RAM usada por todo el sistema.", "", f"Vistas previas: `{previews}/`"]
    report = ws.root / "docs" / "benchmarks" / f"{date}-{args.label}.md"
    report.write_text("\n".join(lines) + "\n")
    print(f"\nInforme: {report}")
    return 0 if all(r["ok"] for r in rows) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="threedai_modeler")
    sub = parser.add_subparsers(dest="group", required=True)

    models = sub.add_parser("models", help="gestionar modelos").add_subparsers(
        dest="action", required=True
    )
    models.add_parser("list").set_defaults(fn=cmd_models_list)
    for name, fn in (("download", cmd_models_download), ("remove", cmd_models_remove)):
        p = models.add_parser(name)
        p.add_argument("model")
        p.set_defaults(fn=fn)

    gen = sub.add_parser("generate", help="imagen -> modelo 3D")
    gen.add_argument("image")
    gen.add_argument("--model", help="ID del modelo (por defecto, el marcado con *)")
    gen.add_argument("--quality", default="standard", help="draft | standard | max | custom")
    gen.add_argument("--base", default="standard", help="con --quality custom: preset de partida")
    gen.add_argument(
        "--set",
        nargs="*",
        default=[],
        metavar="CLAVE=VALOR",
        help="con --quality custom, p. ej. texture_size=8192 shape_steps=40",
    )
    gen.add_argument("--export", default="", help="formatos extra: fbx,obj")
    gen.add_argument(
        "--no-project", action="store_true", help="no proyectar la imagen sobre el modelo"
    )
    gen.add_argument(
        "--faces", default="medium", help="low | medium | high | ultra | none | número"
    )
    gen.add_argument("--seed", type=int)
    gen.add_argument("--name", help="nombre del objeto en Blender (por defecto, el de la imagen)")
    gen.add_argument("--parts", action="store_true", help="separar el modelo en partes (P3-SAM)")
    gen.add_argument("--no-upright", action="store_true", help="no enderezar el modelo")
    gen.add_argument(
        "--parts-detail",
        choices=("normal", "detailed"),
        default="detailed",
        help="detailed: re-segmenta las piezas grandes",
    )
    for view, label in (("left", "izquierda"), ("back", "espalda"), ("right", "derecha")):
        gen.add_argument(f"--{view}", help=f"imagen de la vista {label} (usa el modelo multivista)")
    gen.set_defaults(fn=cmd_generate)

    res = sub.add_parser("resume", help="reanudar un trabajo interrumpido")
    res.add_argument("job")
    res.add_argument("--from", dest="from_stage", help="repetir desde esta etapa y las siguientes")
    res.set_defaults(fn=cmd_resume)

    sub.add_parser("jobs", help="listar trabajos").set_defaults(fn=cmd_jobs)

    bench = sub.add_parser("bench", help="generar un set de imágenes y escribir un informe")
    bench.add_argument("images", nargs="+")
    bench.add_argument("--label", default="bench", help="nombre del informe")
    bench.add_argument("--model")
    bench.add_argument("--quality", default="standard")
    bench.add_argument("--faces", default="medium")
    bench.add_argument("--seed", type=int, default=42)
    bench.add_argument("--parts", action="store_true")
    bench.set_defaults(fn=cmd_bench)

    prev = sub.add_parser("preview", help="renderizar 4 vistas del resultado con Blender")
    prev.add_argument("job")
    prev.add_argument("--size", type=int, default=512)
    prev.set_defaults(fn=cmd_preview)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ws = Workspace.discover()
    reg = Registry.for_workspace(ws)
    return int(args.fn(ws, reg, args))


if __name__ == "__main__":
    sys.exit(main())
