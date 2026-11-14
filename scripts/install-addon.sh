#!/usr/bin/env bash
# Instala el addon en tu Blender sin copiar código fuera del proyecto:
# agrega un "repositorio local" de extensiones que apunta a src/ y habilita el addon.
#
#   ./scripts/install-addon.sh            -> instalar
#   ./scripts/install-addon.sh uninstall  -> deshabilitar y quitar el repositorio
#
# Usa THREEDAI_BLENDER si está definido; si no, blender del PATH o el de Steam.
# Cierra Blender antes de ejecutarlo (Blender guarda sus preferencias al salir).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BLENDER="${THREEDAI_BLENDER:-$(command -v blender || echo "$HOME/.local/share/Steam/steamapps/common/Blender/blender")}"
ACTION="${1:-install}"

if pgrep -x blender >/dev/null; then
    echo "Blender está abierto: ciérralo y vuelve a ejecutar este script." >&2
    exit 1
fi

"$BLENDER" --background --python-exit-code 1 --python-expr "
import bpy, sys
REPO_MODULE = 'threedai'
ADDON = 'bl_ext.' + REPO_MODULE + '.threedai_modeler'
repos = bpy.context.preferences.extensions.repos
existing = next((r for r in repos if r.module == REPO_MODULE), None)
if '$ACTION' == 'uninstall':
    if ADDON in bpy.context.preferences.addons:
        bpy.ops.preferences.addon_disable(module=ADDON)
    if existing is not None:
        repos.remove(existing)
    bpy.ops.wm.save_userpref()
    print('Addon desinstalado.')
    sys.exit(0)
if existing is None:
    existing = repos.new(name='3DAI Modeler (local)', module=REPO_MODULE, custom_directory='$ROOT/src')
existing.custom_directory = '$ROOT/src'
existing.use_custom_directory = True
existing.enabled = True
bpy.ops.extensions.repo_refresh_all()
bpy.ops.preferences.addon_enable(module=ADDON)
if ADDON not in bpy.context.preferences.addons:
    sys.exit('No se pudo habilitar el addon')
bpy.ops.wm.save_userpref()
print('Addon instalado: Viewport 3D > barra lateral (N) > pestaña 3DAI')
" 2>&1 | grep -E "Addon|Error|rror" || true
