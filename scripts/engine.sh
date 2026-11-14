#!/usr/bin/env bash
# Atajos para el motor (ComfyUI en Docker).
#   build  -> construye la imagen mínima
#   setup  -> instala ComfyUI + PyTorch en ./engine
#   up     -> arranca ComfyUI en http://127.0.0.1:8188
#   down   -> lo detiene
#   logs   -> muestra los logs
#   shell  -> abre una terminal dentro del contenedor
#   gpu    -> verifica la GPU
set -euo pipefail

cd "$(dirname "$0")/.."
export HOST_UID="$(id -u)" HOST_GID="$(id -g)"
mkdir -p engine

case "${1:-}" in
    build) docker compose build engine ;;
    setup) docker compose run --rm engine bash /scripts/setup-engine.sh ;;
    up)    docker compose up -d engine && echo "ComfyUI: http://127.0.0.1:8188" ;;
    down)  docker compose down ;;
    logs)  docker compose logs -f engine ;;
    shell) docker compose run --rm engine bash ;;
    gpu)   docker compose run --rm engine python /scripts/gpu-check.py ;;
    *)     sed -n '2,10p' "$0"; exit 1 ;;
esac
