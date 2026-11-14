# Optimización de memoria — 2026-11-14

Equipo: RTX 5060 Ti 16 GB · 15 GiB RAM + 23 GiB swap. Hacha de ejemplo, semilla 42, 50 000 caras.

## Caché del motor (`--cache-none`)

| | Caché por defecto | `--cache-none` |
|---|---|---|
| Tiempo (estándar) | 112 s | 109 s |
| Pico de VRAM | 12.3 GiB | **10.7 GiB** |
| RAM del motor tras el trabajo | 7.7 GiB + 1.6 GiB swap | **5.7 GiB + 0.5 GiB swap** |
| Pico de RAM del motor | 8.9 GiB | 8.8 GiB |

Adoptado. Con la caché por defecto el motor retenía los resultados intermedios (mallas densas,
vóxeles) y el trabajo siguiente arrancaba con la memoria llena; en una prueba el sistema mató el
motor (SIGKILL, `systemd-oomd` activo).

## P3-SAM: fp32 vs bf16

| | fp32 | bf16 |
|---|---|---|
| Tiempo | 54.7 s | **37.2 s** |
| VRAM | 6.44 GiB | **5.85 GiB** |
| Partes | 6 | 7 (una pieza pequeña extra) |
| Coincidencia | — | 98.6 % de las caras |

Adoptado bf16 como predeterminado (`parts_segment.py --precision fp32` para comparar).

## Liberar VRAM antes de hornear

Nodo propio `ThreeDAIFreeVRAM` (`comfy_nodes/threedai_nodes`) entre la generación y el horneado:
descarga los modelos (~6 GiB) para que el horneado tenga la GPU. El normal map 8K igual no cabe:
el horneado solo ocupa ~11 GiB (procesa todos los texeles a la vez) más lo que usa el escritorio.
Tope práctico en 16 GB: normal map 4K, color y materiales 8K.
