#!/usr/bin/env bash
# Instala ComfyUI + PyTorch dentro de /workspace (= ./engine del proyecto).
# Se ejecuta dentro del contenedor: ./scripts/engine.sh setup
set -euo pipefail

COMFY_VERSION="${COMFY_VERSION:-v0.45.3}"
PY_VERSION="${PY_VERSION:-3.12}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu130}"

cd /workspace
mkdir -p "$HOME"

echo ">> ComfyUI $COMFY_VERSION"
if [ ! -d ComfyUI/.git ]; then
    git clone --depth 1 --branch "$COMFY_VERSION" \
        https://github.com/comfyanonymous/ComfyUI.git ComfyUI
else
    git -C ComfyUI fetch --depth 1 origin tag "$COMFY_VERSION"
    git -C ComfyUI checkout -q "$COMFY_VERSION"
fi

echo ">> Python $PY_VERSION (venv en /workspace/.venv)"
[ -x .venv/bin/python ] || uv venv --python "$PY_VERSION" .venv

LOCK=/scripts/requirements.lock
if [ -f "$LOCK" ] && [ "${NO_LOCK:-0}" != 1 ]; then
    echo ">> Dependencias fijadas ($LOCK)"
    uv pip sync --python .venv/bin/python "$LOCK" \
        --extra-index-url "$TORCH_INDEX" --index-strategy unsafe-best-match
else
    # Sin lockfile (o NO_LOCK=1 para actualizar): últimas versiones compatibles.
    # Después de verificar, regenerar el lock con:
    #   uv pip freeze --python .venv/bin/python > /scripts/requirements.lock
    echo ">> PyTorch ($TORCH_INDEX)"
    uv pip install --python .venv/bin/python torch torchvision torchaudio \
        --index-url "$TORCH_INDEX"

    echo ">> Dependencias de ComfyUI"
    uv pip install --python .venv/bin/python -r ComfyUI/requirements.txt
fi

echo ">> Verificación de GPU"
python /scripts/gpu-check.py
