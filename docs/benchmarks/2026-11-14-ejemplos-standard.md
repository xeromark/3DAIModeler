# Benchmark 2026-11-14 — ejemplos-standard

Modelo `pixal3d-int8` · calidad `standard` · 50000 caras · semilla 42 · con partes

| Imagen | Generar | Partes | Pico RAM* | Pico VRAM | Caras | Objetos |
|---|---|---|---|---|---|---|
| weapon_workbench_lowpoly_1787358468668.jpg | 205.3 s | 54.3 s | 10.77 GiB | 12.77 GiB | 49990 | 29 |
| observatory_boss_concept_v1.png | 86.2 s | 45.2 s | 10.18 GiB | 9.76 GiB | 49998 | 13 |
| lobby_concept_final.jpg | 188.6 s | 46.8 s | 10.62 GiB | 11.81 GiB | 49980 | 6 |

\* RAM usada por todo el sistema.

Vistas previas: `engine/bench/2026-11-14-ejemplos-standard/`

## Observaciones

- **Personaje (observatory_boss):** muy fiel; reconstruye la espalda (trenza) de forma coherente.
  Partes excelentes: pelo, torso, brazos, abrigo, pantalón, botas (13 objetos).
- **Mesa de trabajo (weapon_workbench):** buen detalle y 29 partes coherentes. Salía **inclinada
  26.6°** porque Pixal3D genera alineado con la cámara de la imagen (vista isométrica): se agregó
  el enderezado automático (base de apoyo en la envolvente convexa), que lo corrige.
  Los matraces y la tabla de runas pierden el brillo cian: las texturas no incluyen emisión y el
  vidrio sale gris metálico.
- **Escena (lobby):** solo extrae la casa (bien resuelta, enderezada 5.4°); árbol, jardín y arcos se
  pierden. Limitación esperada: un objeto por imagen.
- "Detalle" (upsample a 1536) y "Textura" escalan con la complejidad del objeto: 29–94 s.

## Pendientes que salen de aquí

1. Emisión: detectar zonas brillantes de la imagen y agregarlas como emisión al material.
2. Vidrio/transparencia: hoy todo es opaco.
3. Escenas: separar varios objetos de una imagen (Pixal3D trae un modo de escenas con objetos
   separados) o pedir un objeto por imagen.
