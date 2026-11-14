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
