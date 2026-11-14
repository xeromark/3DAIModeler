# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
El proyecto usa [SemVer](https://semver.org/lang/es/).

## [Unreleased]

### Added

- Motor en Docker: ComfyUI 0.45.3, PyTorch 2.14.1 + CUDA 13.0, modo `--offline`, solo `127.0.0.1`.
- `scripts/engine.sh` para construir, instalar, arrancar y detener el motor.
- `scripts/dev.sh` y hook de pre-commit: ruff, mypy y pytest.
- Esqueleto de la extensión de Blender y `core/paths.py`.
- Lockfile de dependencias del motor.
- CI con lint, tipos y tests unitarios.
- Plan de desarrollo en `docs/PLAN.md`.
- Registro de modelos (`models/registry.toml`) con descarga reanudable y verificación SHA-256.
- Cliente de ComfyUI con progreso por WebSocket (solo biblioteca estándar).
- Workflow Pixal3D imagen → GLB con texturas PBR, presets de calidad y de polígonos.
- Trabajos por etapas guardados en disco y reanudables; métricas de tiempo, RAM y VRAM.
- CLI: `generate`, `resume`, `jobs`, `preview`, `models list|download|remove`.
- Render de vistas previas con Blender en background (`worker/render_preview.py`).
- Etapa `postprocess`: GLB -> `.blend` con texturas empaquetadas, objeto nombrado, origen en la
  base y verificación de polígonos (`worker/postprocess.py`).
- Addon de Blender (pestaña 3DAI): motor (instalar/iniciar/detener/logs), generación con
  progreso y cancelación, descarga de modelos, resultados (importar/reanudar).
- `core/engine.py` (Runner Docker + EngineManager) y `core/tasks.py` (tareas en hilos de fondo).
- `scripts/install-addon.sh`: instala el addon como repositorio local que apunta a `src/`.
- Soporte de TRELLIS.2 (workflow y adaptador) como segundo modelo de generación.
- Panel Modelos: descargar, borrar, uso de disco y variantes agregadas desde Hugging Face
  (revisión fijada, SHA-256 obligatorio, solo `.safetensors`).
- Separación en partes con P3-SAM (Hunyuan3D-Part): etapa `parts` opcional, entorno aislado
  (`./scripts/engine.sh setup-parts`), un objeto por parte en Blender; opción en el addon y en
  la CLI (`generate --parts`).
- Vistas previas con partes coloreadas (`render_preview.py ... --color-parts`).
- Enderezado automático del modelo (Pixal3D sigue la cámara de la imagen): opción "Enderezar"
  en el addon, `--no-upright` en la CLI.
- `bench`: genera un set de imágenes y escribe un informe en `docs/benchmarks/`.
- `resume --from <etapa>`: repite una etapa y las siguientes sin regenerar el modelo.
- Interfaz reorganizada: **Motor** (estado, VRAM/RAM en vivo, modelos en uso, gestionar
  modelos), **Generar** (solo imagen, calidad, polígonos y opciones; avanzado aparte) y
  **Resultados** (miniaturas, importar, reanudar, abrir carpeta, borrar). Textos largos en varias
  líneas, tiempo estimado según trabajos anteriores.
- Agregar modelo **desde archivo**: lee solo la cabecera del `.safetensors`, reconoce la
  arquitectura y la precisión por su huella (`models/signatures.toml`) y, si el archivo es
  idéntico a uno del catálogo, lo instala sin volver a descargarlo. Mover o copiar al proyecto.
- Agregar desde Hugging Face lista los `.safetensors` del repositorio.
- La energía de cada trabajo (Wh, medida con `nvidia-smi`) queda en sus métricas.
- **Multivista:** además del frente, vistas opcionales de izquierda, espalda y derecha (panel
  "Vistas adicionales", CLI `--left/--back/--right`). Con alguna vista se usa automáticamente
  Pixal3D multivista; sin vistas, todo funciona como antes.
- Calidad **Máxima** con texturas de color y materiales en 8K (normal map y oclusión en 4K: a 8K
  el horneado del normal map no cabe en 16 GB de VRAM).
- Calidad **Personalizada**: el panel "Avanzado" (solo visible con ella) ajusta pasos de la IA,
  resolución de detalle y remesh, tamaño de cada textura (1K a 8K), semilla y exportación. Botones
  para partir de Borrador, Estándar o Máxima; los valores quedan guardados en el trabajo.
  CLI: `--quality custom --base max --set texture_size=8192 shape_steps=40`.
- Exportar también **FBX** (texturas embebidas) y **OBJ** (con `.mtl`); las texturas se guardan
  junto al modelo como `model_color.png`, `model_normal.png` y `model_orm.png`.
- Optimización de memoria: `--cache-none` en el motor (−2 GiB de RAM retenida, −1.6 GiB de VRAM
  pico), nodo propio que libera la VRAM antes de hornear, P3-SAM en bf16 (32 % más rápido).
- Panel **Estilos** (visualización en Blender, reversible): Anime / Cel shading (bandas de
  sombra y contorno por casco invertido), Blanco y negro, Colores saturados y Acuarela
  (compositor con filtro Kuwahara). Intensidad ajustable; se ve en el viewport y en el render, y
  los modelos importados después toman el estilo activo.
- Polígonos "Sin reducir": conserva la malla completa del remesh (680 k a 1.6 M caras según
  la calidad), para impresión 3D o esculpir. CLI: `--faces none`.

### Fixed

- El addon detectaba el motor como "no instalado" con el motor detenido (el `python` del venv es
  un enlace que solo existe dentro del contenedor).
- El motor no se detenía al cerrar Blender.
- Blender abierto desde Steam no veía Docker (corre dentro del contenedor de Steam): usa
  `/run/host/usr/bin/docker` y explica cómo exponer el socket.
- Los trabajos creados antes de la etapa de partes aparecían como incompletos.
- "Detener" no detenía el motor con Blender abierto desde Steam: dentro de su contenedor
  `docker compose` no encuentra el plugin; ahora se usa el del sistema directamente. Si aun así
  no se puede detener, el panel lo informa en vez de fallar en silencio.
