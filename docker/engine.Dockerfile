# Imagen mínima del motor (ComfyUI).
# Solo trae el sistema base y uv. Python, PyTorch, ComfyUI, modelos y cachés
# viven en ./engine (montado en /workspace), dentro del proyecto, no en la imagen.
FROM ubuntu:24.04

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git ca-certificates curl build-essential libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Todo lo que se escriba en disco queda en /workspace (= ./engine del proyecto).
ENV HOME=/workspace/.home \
    XDG_CACHE_HOME=/workspace/.cache \
    UV_CACHE_DIR=/workspace/.cache/uv \
    UV_PYTHON_INSTALL_DIR=/workspace/.cache/python \
    HF_HOME=/workspace/.cache/huggingface \
    VIRTUAL_ENV=/workspace/.venv \
    PATH=/workspace/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

WORKDIR /workspace

# Red del host (ver compose.yaml): escuchar solo en 127.0.0.1 para que nadie
# más en la red pueda usar ComfyUI. compose.yaml sobrescribe este comando.
CMD ["python", "ComfyUI/main.py", "--listen", "127.0.0.1", "--port", "8188", "--offline"]
