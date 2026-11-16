"""Segmenta un GLB en partes con P3-SAM. Corre dentro del contenedor con /workspace/parts/.venv.

    /workspace/parts/.venv/bin/python /scripts/parts_segment.py \\
        in.glb out.npz --ckpt p3sam.safetensors

Salida (npz): `centers` (centro de cada cara, float32 Nx3) y `labels` (parte de cada cara, int).
Blender asigna cada cara del modelo texturizado a una parte por cercanía de centros, así que no
depende del orden de las caras. Imprime una línea RESULT con JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import types
from pathlib import Path

import numpy as np
import torch
import trimesh

REPO = Path("/workspace/parts/Hunyuan3D-Part")
os.environ["HF_HUB_OFFLINE"] = "1"  # nunca descargar desde aquí: los pesos vienen del registro
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")


def install_torch_scatter_shim() -> None:
    """Sonata solo usa torch_scatter.segment_csr; PyTorch ya lo trae como segment_reduce."""

    def segment_csr(src: torch.Tensor, indptr: torch.Tensor, reduce: str = "sum") -> torch.Tensor:
        return torch.segment_reduce(src, reduce, offsets=indptr.long(), axis=0)

    module = types.ModuleType("torch_scatter")
    module.segment_csr = segment_csr  # type: ignore[attr-defined]
    sys.modules["torch_scatter"] = module


def load_automask(ckpt: Path):  # type: ignore[no-untyped-def]
    install_torch_scatter_shim()
    for sub in ("XPart/partgen", "P3-SAM", "P3-SAM/demo"):
        sys.path.insert(0, str(REPO / sub))

    from models import sonata  # type: ignore[import-not-found]

    config = json.loads((REPO / "XPart/partgen/config/sonata.json").read_text())
    config["enable_flash"] = False  # sin flash-attn

    def load_local(*_args: object, **_kwargs: object) -> torch.nn.Module:
        # Los pesos de Sonata vienen dentro de p3sam.safetensors: no descargar sonata.pth.
        return sonata.model.PointTransformerV3(**config)

    sonata.load = load_local
    import auto_mask  # type: ignore[import-not-found]

    return auto_mask.AutoMask(str(ckpt))


def refine_large_parts(automask, mesh, face_ids, args):  # type: ignore[no-untyped-def]
    """Vuelve a segmentar cada parte grande sola: con menos geometría P3-SAM separa subpiezas
    que en el modelo completo quedan unidas (p. ej. la hoja y la cabeza de un hacha)."""
    total = len(face_ids)
    next_label = int(face_ids.max()) + 1
    refined = 0
    labels, counts = np.unique(face_ids, return_counts=True)
    for label, count in zip(labels, counts, strict=True):
        if label < 0 or count / total < args.refine:
            continue
        faces = np.flatnonzero(face_ids == label)
        sub = mesh.submesh([faces], append=True, repair=False)
        sub.merge_vertices()
        try:
            _aabb, sub_ids, _ = automask.predict_aabb(
                sub,
                point_num=args.points,
                prompt_num=args.prompts,
                threshold=args.threshold,
                post_process=True,
                clean_mesh_flag=False,
                is_parallel=False,
                show_info=False,
                seed=args.seed,
                prompt_bs=args.prompt_batch,
            )
        except Exception as err:  # una pieza difícil no debe tumbar todo
            print(f"refine: parte {label} sin refinar ({err})")
            continue
        sub_ids = np.asarray(sub_ids)
        if len(sub_ids) != len(faces):
            continue
        sub_labels, sub_counts = np.unique(sub_ids[sub_ids >= 0], return_counts=True)
        # Aceptar solo si salen al menos 2 piezas relevantes (>= 5 % de la parte).
        if (sub_counts / len(sub_ids) >= 0.05).sum() < 2:
            continue
        keep = sub_labels[np.argmax(sub_counts)]  # la subpieza mayor conserva la etiqueta
        for sub_label in sub_labels:
            if sub_label != keep:
                face_ids[faces[sub_ids == sub_label]] = next_label
                next_label += 1
        refined += 1
    return face_ids, refined


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("glb", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--ckpt", type=Path, required=True)
    parser.add_argument("--points", type=int, default=100_000)
    parser.add_argument("--prompts", type=int, default=400)
    parser.add_argument("--threshold", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--prompt-batch", type=int, default=4, help="prompts por lote (más = más VRAM y velocidad)"
    )
    parser.add_argument(
        "--refine",
        type=float,
        default=0.0,
        help="re-segmentar las partes con más de esta fracción de caras (0 = no refinar)",
    )
    parser.add_argument(
        "--precision",
        choices=("bf16", "fp32"),
        default="bf16",
        help="bf16: mitad de VRAM y más rápido; fp32: precisión completa",
    )
    args = parser.parse_args()

    started = time.monotonic()
    mesh = trimesh.load(args.glb, force="mesh", process=False)
    # Fusionar vértices duplicados en las costuras de UV sin cambiar el orden de las caras.
    mesh = trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces, process=False)
    mesh.merge_vertices()

    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    automask = load_automask(args.ckpt)
    autocast = torch.autocast("cuda", dtype=torch.bfloat16, enabled=args.precision == "bf16")
    with autocast:
        _aabb, face_ids, out_mesh = automask.predict_aabb(
            mesh,
            point_num=args.points,
            prompt_num=args.prompts,
            threshold=args.threshold,
            post_process=True,
            clean_mesh_flag=False,
            is_parallel=False,
            show_info=False,
            seed=args.seed,
            prompt_bs=args.prompt_batch,
        )
    face_ids = np.asarray(face_ids).copy()
    refined = 0
    if args.refine > 0:
        with autocast:
            face_ids, refined = refine_large_parts(automask, out_mesh, face_ids, args)
    centers = np.asarray(out_mesh.triangles_center, dtype=np.float32)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out, centers=centers, labels=face_ids.astype(np.int32))

    labels, counts = np.unique(face_ids[face_ids >= 0], return_counts=True)
    result = {
        "parts": len(labels),
        "faces": len(face_ids),
        "unassigned_faces": int(np.sum(face_ids < 0)),
        "largest_part_faces": int(counts.max()) if len(counts) else 0,
        "seconds": round(time.monotonic() - started, 1),
        "peak_vram_gib": round(torch.cuda.max_memory_allocated() / 1024**3, 2),
        "precision": args.precision,
        "refined_parts": refined,
    }
    print("RESULT " + json.dumps(result))


if __name__ == "__main__":
    main()
