# 3DAIModeler

Herramienta local para convertir una imagen (o texto) en un modelo 3D listo para Blender (`.blend`),
separado en partes y con cantidad de polígonos controlada. Prioriza calidad sobre velocidad.

## Objetivos

1. **Entrada:** imagen (o texto → imagen con un modelo local).
2. **Forma + textura:** Pixal3D por defecto, TRELLIS.2 como alternativa. Calidad máxima aunque tarde.
3. **Partes:** Hunyuan3D-Part (P3-SAM + X-Part). Cada parte es un objeto separado en Blender.
4. **Polígonos:** presets (bajo / medio / alto) + valor manual, con bake alta → baja para conservar detalle.
5. **Salida:** `.blend` con partes y materiales PBR.
6. **Recursos:** funcionar en 15 GB de RAM y 16 GB de VRAM (un modelo a la vez, pesos INT8).
   Con más RAM se pasa a bf16 cambiando solo la configuración.

## Arquitectura

```
Blender addon (interfaz)
    │  HTTP
    ▼
ComfyUI en Docker, solo 127.0.0.1 (motor de IA)
    ├─ Pixal3D / TRELLIS.2   → forma + textura
    └─ Hunyuan3D-Part        → partes
    ▼
Blender headless (bpy) → reducción de polígonos + bake → .blend
```

## Fases

1. Base: Docker + ComfyUI en la GPU.
2. Generación: imagen → Pixal3D → GLB.
3. Blender: GLB → polígonos + bake → `.blend`.
4. Partes con Hunyuan3D-Part.
5. Addon de Blender.
6. Pulido: comparar modelos, multivista, selección de semillas.

## Estructura

```
src/threedai_modeler/     Extensión de Blender (core/ = lógica pura, sin bpy)
tests/                    Tests (unit/ sin GPU; integration/ con el motor)
compose.yaml              Servicio del motor (GPU, red del host, sin root)
docker/engine.Dockerfile  Imagen mínima: Ubuntu + uv
scripts/engine.sh         Motor: build, setup, up, down, logs, shell, gpu
scripts/dev.sh            Desarrollo: setup, check, fix, test
scripts/container/        Scripts del contenedor + requirements.lock
docs/PLAN.md              Plan de desarrollo, requisitos, hitos y decisiones
engine/                   (ignorado por git) ComfyUI, venv, modelos, cachés, resultados
```

Todo lo que el motor instala o descarga queda en `engine/`. Fuera del proyecto solo queda la
imagen de Docker (~1 GB en `/var/lib/docker`).

## Uso

```bash
./scripts/engine.sh build   # una vez
./scripts/engine.sh setup   # instala ComfyUI + PyTorch en engine/
./scripts/engine.sh gpu     # verifica la GPU
./scripts/engine.sh setup-parts   # opcional: soporte de partes (P3-SAM) en engine/parts
./scripts/engine.sh up      # ComfyUI en http://127.0.0.1:8188
./scripts/engine.sh down
```

## Addon de Blender

```bash
./scripts/install-addon.sh             # con Blender cerrado
./scripts/install-addon.sh uninstall
```

**Blender de Steam:** Steam lo ejecuta dentro de su contenedor, que oculta Docker. Agrega en
Steam → Blender → Propiedades → Opciones de lanzamiento:
`PRESSURE_VESSEL_FILESYSTEMS_RW=/run/docker.sock %command%`

En Blender: Viewport 3D → barra lateral (`N`) → pestaña **3DAI**. Desde ahí se instala, inicia y
detiene el motor, se descargan modelos y se genera. El código no se copia fuera del proyecto: el
addon se carga desde `src/` como repositorio local de extensiones.

## Desarrollo

```bash
./scripts/dev.sh setup   # Python 3.13 + ruff/mypy/pytest en .venv, activa el hook de pre-commit
./scripts/dev.sh check   # lo mismo que corre el hook y la CI
```

## Seguridad

- El contenedor corre con tu usuario, sin capacidades extra (`cap_drop: ALL`) y solo ve `engine/`.
- ComfyUI escucha solo en `127.0.0.1`.
- Modelos solo desde cuentas oficiales (`Comfy-Org/`, `microsoft/`, `tencent/`, `TencentARC/`),
  en `.safetensors` siempre que exista.

## Nota de red

La red "bridge" de Docker no tiene salida a internet en este PC, así que el motor usa la red del host.
