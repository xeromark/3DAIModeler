#!/usr/bin/env bash
# Entorno de desarrollo del proyecto (en el host, no en el contenedor).
# Mantiene la caché de uv, los Python y el venv dentro del workspace.
# Usa engine/.cache/dev/, separado de las cachés del contenedor (engine/.cache/).
#
#   ./scripts/dev.sh setup   -> instala Python 3.13 + herramientas y activa el git hook
#   ./scripts/dev.sh check   -> lint + formato + tipos + tests unitarios
#   ./scripts/dev.sh fix     -> corrige lint y formato automáticamente
#   ./scripts/dev.sh test    -> tests unitarios
#   ./scripts/dev.sh cli ... -> CLI del proyecto (models list|download|remove, generate)
#   ./scripts/dev.sh uv ...  -> cualquier comando de uv con este entorno
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export UV_CACHE_DIR="$ROOT/engine/.cache/dev/uv"
export UV_PYTHON_INSTALL_DIR="$ROOT/engine/.cache/dev/python"
export UV_PYTHON_INSTALL_BIN=0          # no crear enlaces en ~/.local/bin
export UV_PYTHON_PREFERENCE=only-managed
export UV_PROJECT_ENVIRONMENT="$ROOT/.venv"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

case "${1:-}" in
    setup)
        uv python install 3.13
        uv sync
        git config core.hooksPath scripts/git-hooks
        echo "Listo. Hook de pre-commit activo."
        ;;
    check)
        uv run ruff check .
        uv run ruff format --check .
        uv run mypy
        uv run pytest
        ;;
    fix)
        uv run ruff check --fix .
        uv run ruff format .
        ;;
    test) shift; uv run pytest "$@" ;;
    cli)  shift; uv run python -m threedai_modeler.cli "$@" ;;
    uv)   shift; uv "$@" ;;
    *)    sed -n '2,11p' "$0"; exit 1 ;;
esac
