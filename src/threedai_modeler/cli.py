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
from .core.jobs import Job
from .core.paths import Workspace
from .core.pipeline import Pipeline, PipelineError, Progress
from .core.presets import Presets
from .core.registry import Registry


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
    pipeline = Pipeline(ws, reg, Presets.load(ws.presets_file), ComfyClient())
    last = {"label": "", "pct": -1}

    def progress(p: Progress) -> None:
        pct = -1 if p.fraction is None else int(p.fraction * 100)
        if p.label != last["label"] or (pct >= 0 and pct >= last["pct"] + 10):
            suffix = f" {pct:3d}%" if pct >= 0 else ""
            print(f"  [{time.strftime('%H:%M:%S')}] {p.label}{suffix}", flush=True)
            last["label"], last["pct"] = p.label, pct

    cancel = threading.Event()
    print(f"Trabajo {job.id} ({job.model_id}, {job.quality}, {job.target_faces} caras)")
    try:
        pipeline.run(job, progress, cancel)
    except KeyboardInterrupt:
        cancel.set()
        print("Cancelado. Reanudar con: resume", job.id)
        return 130
    except (PipelineError, ComfyError) as err:
        print(f"Error: {err}", file=sys.stderr)
        print(f"Reanudar con: resume {job.id}", file=sys.stderr)
        return 1
    glb = job.output_path("generate", "glb")
    print(f"Listo: {glb}")
    print(json.dumps(job.stages["generate"].metrics, indent=2, ensure_ascii=False))
    return 0


def cmd_generate(ws: Workspace, reg: Registry, args: argparse.Namespace) -> int:
    presets = Presets.load(ws.presets_file)
    presets.get_quality(args.quality)
    default = reg.default_model()
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
    )
    return _run_job(ws, reg, job)


def cmd_resume(ws: Workspace, reg: Registry, args: argparse.Namespace) -> int:
    return _run_job(ws, reg, Job.load(ws, args.job))


def cmd_jobs(ws: Workspace, _reg: Registry, _args: argparse.Namespace) -> int:
    for job in Job.list_all(ws):
        states = " ".join(f"{n}={r.status.value}" for n, r in job.stages.items())
        print(f"{job.id}  {job.model_id:16} {job.quality:9} {job.target_faces:>8} caras  {states}")
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
    gen.add_argument("--quality", default="standard", help="draft | standard | max")
    gen.add_argument("--faces", default="medium", help="low | medium | high | ultra | número")
    gen.add_argument("--seed", type=int)
    gen.set_defaults(fn=cmd_generate)

    res = sub.add_parser("resume", help="reanudar un trabajo interrumpido")
    res.add_argument("job")
    res.set_defaults(fn=cmd_resume)

    sub.add_parser("jobs", help="listar trabajos").set_defaults(fn=cmd_jobs)

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
