#!/usr/bin/env bash
# Instala el soporte de partes (P3-SAM de Hunyuan3D-Part) en /workspace/parts (= ./engine/parts).
# Entorno Python aparte del de ComfyUI para no mezclar dependencias.
# Se ejecuta dentro del contenedor: ./scripts/engine.sh setup-parts
set -euo pipefail

REPO_URL="https://github.com/Tencent-Hunyuan/Hunyuan3D-Part.git"
REPO_COMMIT="${PARTS_COMMIT:-e96be065375438962375b55326416291342958a7}"
PY_VERSION="${PY_VERSION:-3.12}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu130}"
PARTS=/workspace/parts

mkdir -p "$PARTS" "$HOME"
cd "$PARTS"

echo ">> Hunyuan3D-Part @ ${REPO_COMMIT:0:12}"
if [ ! -d Hunyuan3D-Part/.git ]; then
    git init -q Hunyuan3D-Part
    git -C Hunyuan3D-Part remote add origin "$REPO_URL"
fi
git -C Hunyuan3D-Part fetch -q --depth 1 origin "$REPO_COMMIT"
git -C Hunyuan3D-Part checkout -q FETCH_HEAD

echo ">> Python $PY_VERSION (venv en $PARTS/.venv)"
[ -x .venv/bin/python ] || uv venv -q --python "$PY_VERSION" .venv

echo ">> PyTorch (misma versión que el motor)"
TORCH_VERSION="$(/workspace/.venv/bin/python -c 'import torch; print(torch.__version__.split("+")[0])')"
uv pip install --python .venv/bin/python "torch==$TORCH_VERSION" --index-url "$TORCH_INDEX"

echo ">> Dependencias de P3-SAM"
# torch_scatter no se instala: el worker lo reemplaza por torch.segment_reduce.
# flash-attn no se instala: Sonata se construye con enable_flash=False.
uv pip install --python .venv/bin/python \
    "spconv-cu126==2.3.8" timm addict huggingface_hub packaging safetensors \
    trimesh fpsample numba scikit-learn tqdm omegaconf "numpy<2.3"

echo ">> Verificación"
.venv/bin/python - <<'EOF'
import spconv.pytorch, torch, trimesh, fpsample, numba  # noqa: F401
print("parts OK | torch", torch.__version__, "| GPU", torch.cuda.get_device_name(0))
EOF
