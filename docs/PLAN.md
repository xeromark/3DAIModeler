# Plan de desarrollo — 3DAIModeler

Versión del plan: 1.0 · Fecha: 2026-11-14

## 1. Resumen

Addon de Blender que genera modelos 3D desde una imagen (o texto) usando modelos de IA locales.
El usuario solo usa Blender: el addon instala, arranca y detiene el motor de IA, gestiona los
modelos y entrega el resultado en la escena como objetos separados por partes, con cantidad de
polígonos controlada.

Prioridad: **calidad sobre velocidad**. Restricción: **funcionar optimizado con 15 GB de RAM y
16 GB de VRAM**, y escalar con más RAM solo cambiando configuración.

## 2. Alcance

### Objetivos

- Imagen → modelo 3D texturizado (PBR) dentro de Blender.
- Separación automática en partes, cada una como objeto independiente.
- Control de polígonos: presets (bajo / medio / alto) y valor manual, conservando detalle con bake.
- Gestión completa desde el addon: instalar, arrancar, detener, ver estado y logs del motor.
- Gestor de modelos en el addon: listar, descargar, borrar, elegir modelo por etapa y agregar variantes.
- Todo dentro del workspace del proyecto, todo local y sin enviar datos a servicios externos.

### Fuera de alcance (por ahora)

- Generación masiva o en lote para muchos usuarios.
- Servicio web o multiusuario.
- Rigging y animación (posible fase futura).
- Entrenar o hacer fine-tuning de modelos.
- Windows y macOS (solo Linux en v1).

## 3. Requisitos

### Funcionales

| ID | Requisito |
|---|---|
| RF-01 | Generar un modelo 3D texturizado desde una imagen. |
| RF-02 | Generar desde varias imágenes del mismo objeto (multivista). |
| RF-03 | Generar desde texto, pasando por un modelo local de texto → imagen. |
| RF-04 | Separar el modelo en partes; cada parte es un objeto con nombre propio en Blender. |
| RF-05 | Elegir la cantidad de polígonos por preset o valor manual (total o por parte). |
| RF-06 | Conservar el detalle al reducir polígonos con bake de normal map y textura (alta → baja). |
| RF-07 | Importar el resultado en la escena actual y/o guardarlo como `.blend`. |
| RF-08 | Instalar el motor desde el addon (primera vez), con progreso visible. |
| RF-09 | Arrancar y detener el motor desde el addon; ver estado y logs. |
| RF-10 | Listar, descargar y borrar modelos; mostrar tamaño y espacio en disco usado. |
| RF-11 | Elegir qué modelo usa cada etapa (forma, textura, partes). |
| RF-12 | Agregar variantes de modelos de familias soportadas desde Hugging Face. |
| RF-13 | Cancelar un trabajo en curso. |
| RF-14 | Reanudar un trabajo interrumpido desde la última etapa completada. |
| RF-15 | Generar varias semillas y elegir la mejor antes de continuar el pipeline. |

### No funcionales

| ID | Requisito |
|---|---|
| RNF-01 | **Memoria:** pico de RAM del sistema < 13 GB con Blender abierto; nunca dos modelos pesados cargados a la vez. |
| RNF-02 | **Interfaz:** Blender nunca se congela; todo el trabajo pesado corre en procesos aparte. |
| RNF-03 | **Privacidad:** ninguna imagen ni modelo sale del PC (ComfyUI en `--offline`, solo en `127.0.0.1`). |
| RNF-04 | **Seguridad:** modelos solo de fuentes aprobadas, `.safetensors` por defecto, verificación SHA-256 y contenedor sin privilegios. |
| RNF-05 | **Orden:** todo lo del proyecto vive en el workspace (ver §6). |
| RNF-06 | **Reproducibilidad:** versiones fijadas (ComfyUI, PyTorch, dependencias, revisiones de modelos). |
| RNF-07 | **Escalabilidad:** pasar de INT8 a bf16 o cambiar de modelo es configuración, no código. |
| RNF-08 | **Portabilidad del motor:** cambiar Docker por instalación nativa sin tocar el addon. |
| RNF-09 | **Robustez:** si el motor falla, Blender no se cae ni se pierde la escena. |
| RNF-10 | **Mantenibilidad:** lógica separada de la interfaz y testeable sin Blender ni GPU. |

## 4. Arquitectura

### 4.1 Componentes

```
┌─────────────────────────── Blender 5.x ───────────────────────────┐
│  Addon threedai_modeler                                           │
│   ├─ ui/        Paneles: Motor · Modelos · Generar · Resultados   │
│   ├─ ops/       Operadores (no bloqueantes, bpy.app.timers)       │
│   └─ core/      Lógica pura en Python stdlib (sin bpy)            │
│        ├─ engine/    EngineManager + Runner (Docker | Nativo)     │
│        ├─ registry   Catálogo de modelos + descargas              │
│        ├─ comfy      Cliente HTTP/WebSocket de ComfyUI            │
│        ├─ workflows  Plantillas de workflow + parámetros          │
│        └─ jobs       Pipeline por etapas, estado en disco         │
└───────────────┬──────────────────────────────┬────────────────────┘
                │ subprocess: docker compose   │ subprocess:
                │ HTTP 127.0.0.1:8188          │ blender --background
                ▼                              ▼
┌──────── Motor (contenedor) ────────┐  ┌──── Worker de Blender ────┐
│ ComfyUI --offline                  │  │ worker/postprocess.py     │
│  ├─ Pixal3D / TRELLIS.2 (nativos)  │  │  decimate / remesh        │
│  ├─ Hunyuan3D-Part (nodo propio)   │  │  UV + bake alta → baja    │
│  └─ engine/ComfyUI/models/         │  │  partes → objetos → .blend│
└────────────────────────────────────┘  └───────────────────────────┘
```

**Reglas de diseño:**

- `core/` usa solo la biblioteca estándar de Python 3.13 (`urllib`, `json`, `tomllib`, `subprocess`,
  `hashlib`) para correr dentro de Blender sin instalar dependencias, y también fuera de Blender
  (CLI y tests).
- `ui/` y `ops/` son una capa delgada: leen el estado de `core/` y le delegan acciones.
- El postproceso pesado (bake, decimate) corre en un **Blender aparte en modo background** usando
  el mismo binario (`bpy.app.binary_path`). Así la interfaz no se congela y un fallo no tumba la
  escena (RNF-02, RNF-09).

### 4.2 Motor: ciclo de vida

```
NOT_INSTALLED ──install──▶ INSTALLING ──ok──▶ STOPPED ──start──▶ STARTING ──/system_stats ok──▶ READY
                               │                ▲                                        │   ▲
                               ▼                │                                   job  ▼   │ fin
                             ERROR ◀──fallo─────┴──────────── STOPPING ◀──stop───── BUSY ────┘
```

- `EngineManager` expone `status()`, `install()`, `start()`, `stop()`, `logs()`, `free_memory()`.
- `Runner` es una interfaz con dos implementaciones:
  - `DockerRunner`: usa `docker compose -f <workspace>/compose.yaml ...`. Es la implementación actual.
  - `NativeRunner`: `uv` + `bwrap`. Es futura y cumple RNF-08.
- Opciones del addon: "detener el motor al cerrar Blender" (activa por defecto) y "descargar los
  modelos de memoria al terminar cada trabajo" (activa por defecto).
- El estado se detecta, no se supone: el contenedor existe o no, y `/system_stats` responde o no.

### 4.3 Flujo de un trabajo

```
Usuario: imagen + preset ─▶ Job creado en engine/jobs/<id>/job.json
  1. preprocess   quitar fondo, recortar, escalar        (motor)
  2. shape        Pixal3D → malla densa                  (motor)  ─┐ varias semillas:
  3. select       el usuario elige la mejor (opcional)   (addon)  ─┘ previews en el panel
  4. texture      TRELLIS.2/Pixal3D texture stage        (motor)
  5. parts        P3-SAM segmenta · X-Part completa      (motor)
  6. free         POST /free  (descarga modelos de VRAM/RAM)
  7. postprocess  decimate/remesh + UV + bake + .blend   (worker Blender)
  8. import       objetos a la escena actual             (addon)
```

- Cada etapa escribe su salida en `engine/jobs/<id>/<n>_<etapa>/` y marca su estado en `job.json`.
  Reanudar consiste en saltar las etapas completadas (RF-14).
- **Presupuesto de memoria (RNF-01):** solo hay un modelo pesado cargado a la vez. Antes del paso 7
  se libera la memoria del motor. El worker de Blender y el motor nunca trabajan en paralelo.

### 4.4 Registro de modelos

El catálogo oficial está en `models/registry.toml` (versionado en git). Los modelos agregados por el
usuario van en `engine/user-registry.toml`.

```toml
[[model]]
id        = "pixal3d-int8"
family    = "pixal3d"            # define qué adaptador/workflow lo usa
roles     = ["shape", "texture"]
precision = "int8"
license   = "MIT"
vram_gb   = 8                    # estimado, se mide en el benchmark
  [[model.files]]
  repo     = "Comfy-Org/Pixal3D"
  revision = "<commit fijado>"
  path     = "diffusion_models/pixal3d_int8_convrot.safetensors"
  dest     = "diffusion_models"  # carpeta dentro de ComfyUI/models
  sha256   = "<hash>"
  size     = 5_580_000_000
```

- Los archivos compartidos (VAE, DINOv3) se declaran una vez y los modelos los referencian. Al
  borrar un modelo solo se borran los archivos que ningún otro usa.
- **Agregar un modelo (RF-12):** el usuario indica el repositorio y el archivo de Hugging Face, y
  elige una familia soportada. Validaciones:
  - Solo se aceptan `.safetensors`. Otro formato requiere confirmación explícita.
  - Un repositorio fuera de la lista de organizaciones aprobadas muestra un aviso y pide confirmación.
  - La revisión queda fijada al commit actual.
  - El SHA-256 se calcula al descargar y se guarda.
- Familias soportadas en v1: `pixal3d`, `trellis2`, `hunyuan3d-part`. Agregar una familia nueva
  requiere un adaptador en `core/workflows/` y una plantilla de workflow.

### 4.5 Workflows

- Las plantillas de ComfyUI en formato API se guardan en `workflows/<familia>/<etapa>.json` y se
  versionan en git.
- Los parámetros (semilla, resolución, pasos, modelo) se inyectan por nombre de nodo, nunca por ID
  numérico frágil.
- Los presets de calidad están en `models/presets.toml`, por ejemplo
  `draft` / `standard` / `max`. Cada preset fija resolución, pasos, número de semillas y tamaño de
  textura.

## 5. Tecnologías

| Pieza | Elección | Motivo |
|---|---|---|
| Lenguaje | Python 3.13 (Blender), 3.12 (motor) | Es el ecosistema de los modelos y de `bpy`. |
| Motor | ComfyUI 0.45.3 (fijado) | Pixal3D y TRELLIS.2 nativos, gestión de memoria, nodos de malla. |
| Aislamiento | Docker + nvidia-container-toolkit | Ya instalado; Runner permite pasar a nativo. |
| PyTorch | 2.14.1 + cu130 | Soporta `sm_120` (RTX 5060 Ti). |
| Addon | Extensión de Blender (`blender_manifest.toml`) | Formato oficial desde Blender 4.2. |
| Config | TOML (`tomllib` stdlib) | Legible y sin dependencias. |
| Calidad de código | ruff (lint + formato), mypy estricto en `core/` | Estándar; mypy es Python puro y no necesita Node. |
| Tests | pytest | Estándar. |

## 6. Estructura del repositorio

```
3DAIModeler/
├─ src/threedai_modeler/        Paquete = extensión de Blender
│  ├─ blender_manifest.toml
│  ├─ __init__.py               register/unregister (importa bpy solo aquí y en ui/ops)
│  ├─ core/                     Lógica pura, stdlib, sin bpy
│  │  ├─ engine/                manager.py, runner.py, docker_runner.py
│  │  ├─ registry.py
│  │  ├─ comfy.py
│  │  ├─ workflows/             adaptadores por familia
│  │  ├─ jobs.py
│  │  └─ paths.py               raíz del workspace y rutas derivadas
│  ├─ ui/                       paneles
│  ├─ ops/                      operadores
│  └─ cli.py                    `python -m threedai_modeler.cli ...` (pruebas sin Blender)
├─ worker/postprocess.py        Se ejecuta con `blender --background --python`
├─ workflows/                   Plantillas ComfyUI (JSON API)
├─ models/registry.toml         Catálogo oficial
├─ models/presets.toml          Presets de calidad y polígonos
├─ docker/engine.Dockerfile
├─ compose.yaml
├─ scripts/                     engine.sh, scripts del contenedor, dev-link del addon
├─ tests/unit/                  Sin GPU ni Blender (corre en CI)
├─ tests/integration/           Con motor y GPU (marcados, locales)
├─ tests/fixtures/              Imágenes de prueba y benchmark
├─ docs/PLAN.md · docs/adr/     Plan y decisiones de arquitectura
└─ engine/                      (gitignored) ComfyUI, venv, modelos, cachés, jobs, logs
```

**Instalar el addon sin salir del workspace:** en Blender, en Preferencias → Get Extensions →
Repositories, se agrega un *repositorio local* que apunta a `src/`. El código no se copia fuera del
proyecto. El addon calcula la raíz del workspace desde su propia ubicación, y se puede
sobrescribir en las preferencias.

## 7. Prácticas de desarrollo

### Git

- `main` siempre funcional. Cada hito o tarea va en una rama (`feat/m1-registry`, `fix/...`) y se
  integra con un PR pequeño.
- Mensajes de commit con *Conventional Commits* (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).
- Versionado SemVer: `0.x` hasta cumplir el alcance de v1. Cada versión se registra en `CHANGELOG.md`.
- Nunca se suben pesos, cachés ni el venv (`engine/` está en `.gitignore`).

### Código

- Type hints en todo `core/`. `mypy --strict` en `core/`.
- `./scripts/dev.sh check` (ruff, mypy, pytest) corre antes de cada commit vía `scripts/git-hooks/pre-commit`.
- Errores con mensajes accionables para el usuario, por ejemplo: "Docker no está corriendo: inicia
  el servicio con `systemctl start docker`".
- Logging con el módulo `logging`, a `engine/logs/` y al panel del addon.
- Sin rutas absolutas en el código: todo se deriva de `core/paths.py`.
- Las dependencias del motor se fijan con un lockfile (`uv pip compile` → `scripts/container/requirements.lock`).

### Decisiones

- Cada decisión de arquitectura relevante se registra como ADR en `docs/adr/NNNN-titulo.md`
  (contexto, decisión, consecuencias). Las decisiones ya tomadas se migran desde §10.

### CI (GitHub Actions)

- Lint y tests unitarios en cada PR. No hay GPU en CI: los tests de integración corren en local.

## 8. Estrategia de pruebas

| Nivel | Qué cubre | Dónde corre |
|---|---|---|
| Unitario | registry, presets, jobs (estado y reanudación), parámetros de workflows, Runner con `subprocess` simulado | CI + local |
| Integración | motor real: install/start/stop, ejecutar cada workflow con una imagen pequeña | Local (GPU) |
| Worker | `blender --background` sobre una malla fija: polígonos dentro de ±5 % del objetivo, `.blend` válido | Local |
| End-to-end | imagen → `.blend` desde la CLI y desde el addon | Local |
| Benchmark | ~10 imágenes fijas en `tests/fixtures/bench/`: tiempo por etapa, pico de RAM y VRAM, polígonos y renders de comparación | Local, en cada cambio de modelo o preset |

El benchmark es la herramienta para "irlo puliendo": cada cambio de modelo, parámetro o preset se
compara contra la línea base guardada en `docs/benchmarks/`.

## 9. Hitos

Cada hito termina con algo usable, tests en verde y criterios de aceptación cumplidos.

### M0 — Fundaciones *(completo)*

- [x] Motor en Docker con GPU (ComfyUI 0.45.3, PyTorch cu130, `--offline`, solo `127.0.0.1`).
- [x] `README.md` y `.gitignore`.
- [x] Estructura de §6, `pyproject.toml` (ruff, pytest, mypy) y hook de pre-commit.
- [x] Lockfile de dependencias del motor (`scripts/container/requirements.lock`).
- [x] Primer commit y push.

**Aceptación:** `ruff` y `pytest` pasan en limpio. `./scripts/engine.sh up` deja ComfyUI respondiendo.

### M1 — Núcleo y generación desde CLI *(en curso)*

- [x] `core/paths.py`, `core/registry.py` (catálogo, descarga con SHA-256, revisión fijada, borrado seguro).
- [x] `models/registry.toml` con Pixal3D INT8 y archivos compartidos.
- [x] `core/comfy.py`: encolar un workflow, seguir el progreso (WebSocket), descargar los resultados y llamar a `/free`.
- [x] `core/jobs.py`: etapas, estado en disco y reanudación.
- [x] Workflow `pixal3d` imagen → GLB texturizado (basado en la plantilla oficial; incluye remesh,
  decimate, UVs y bake de color/normal/AO).
- [x] CLI: `generate`, `resume`, `jobs`, `preview`, `models list|download|remove`.
- [x] Primera línea base con una imagen (`docs/benchmarks/2026-11-14-baseline.md`).
- [ ] Set de benchmark con ~10 imágenes propias en `tests/fixtures/bench/`.

**Aceptación:** `python -m threedai_modeler.cli generate foto.png` produce un GLB texturizado.
El pico de RAM cumple RNF-01 y queda medido.

### M2 — Postproceso en Blender

- `worker/postprocess.py`: importa el GLB, limpia la malla, reduce polígonos (decimate o QuadriFlow
  según el preset), despliega UVs, hornea normal map y albedo (alta → baja) y guarda el `.blend`.
- `models/presets.toml`: presets de polígonos y tamaño de textura.

**Aceptación:** los polígonos quedan dentro de ±5 % del objetivo. El `.blend` abre con texturas
empaquetadas. La comparación visual entre alta y baja queda registrada en el benchmark.

### M3 — Addon MVP

- Extensión con su manifiesto e instalación por repositorio local.
- Panel **Motor:** Instalar / Iniciar / Detener, estado, logs y uso de disco.
- Panel **Generar:** imagen, preset de calidad, polígonos y botón Generar con progreso por etapa.
  Incluye cancelar.
- Importación del resultado a la escena.
- Comprobación inicial: Docker disponible desde el Blender de Steam (ver riesgo R5).

**Aceptación:** desde un Blender recién abierto se llega a un modelo en escena sin usar la terminal,
y la interfaz no se congela en ningún momento.

### M4 — Gestor de modelos

- Panel **Modelos:** catálogo, estado (descargado o no), tamaño, licencia, descargar y borrar.
- Elección de modelo por etapa (forma, textura, partes).
- Agregar una variante: repositorio y archivo de Hugging Face, familia, y las validaciones de §4.4.
- TRELLIS.2 como segundo modelo, para comparar contra Pixal3D en el benchmark.

**Aceptación:** se puede cambiar de Pixal3D a TRELLIS.2 desde la interfaz, sin código. Agregar
Pixal3D bf16 como variante funciona de punta a punta.

### M5 — Partes

- Integrar Hunyuan3D-Part (P3-SAM + X-Part light) como nodo propio de ComfyUI, compilando sus
  dependencias para `sm_120`.
- Worker: cada parte es un objeto con nombre, con presupuesto de polígonos repartido y su propio bake.
- Opción en la interfaz: modelo entero o separado en partes.

**Aceptación:** un modelo de prueba (por ejemplo, una silla o un personaje simple) sale con partes
separadas y coherentes, y cumple RNF-01.

### M6 — Calidad

- Multivista con Pixal3D (RF-02).
- Varias semillas con previews y elección en el panel (RF-15).
- Upscale de texturas a 4K.
- Presets `draft` / `standard` / `max`, ajustados con el benchmark.
- Texto → imagen local (RF-03); el modelo concreto se decide en un ADR.

**Aceptación:** el preset `max` supera la línea base de M1 en el benchmark (comparación visual
documentada).

### M7 — Robustez y v1.0

- Reanudación de trabajos desde la interfaz, mensajes de error revisados y limpieza de trabajos viejos.
- Documentación de usuario.
- Revisión de seguridad del contenedor y de las descargas.
- `CHANGELOG.md`, etiqueta `v1.0.0`.

## 10. Decisiones tomadas

| ID | Decisión | Motivo |
|---|---|---|
| D1 | Python en todo el proyecto | Ecosistema de los modelos y de `bpy`. |
| D2 | ComfyUI como motor | Modelos nativos, gestión de memoria, intercambiable por nodos. |
| D3 | Docker ahora, con interfaz Runner | Aislamiento; se puede pasar a nativo sin tocar el addon. |
| D4 | Red del host en Docker | La red bridge no tiene salida a internet en este PC. ComfyUI escucha solo en `127.0.0.1`. |
| D5 | `--offline` | Sin nodos de pago en la nube; privacidad. |
| D6 | INT8 por defecto, bf16 al tener más RAM | RNF-01 y RNF-07. |
| D7 | Pixal3D por defecto, TRELLIS.2 como alternativa | Mejor fidelidad a la imagen y soporte de multivista. |
| D8 | Hunyuan3D-2.1 Paint pospuesto | Requiere ~21 GB de VRAM. |
| D9 | Postproceso en un Blender aparte (background) | La interfaz no se congela y los fallos quedan aislados. |
| D10 | `core/` solo con stdlib | Corre dentro de Blender sin instalar dependencias. |
| D11 | Todo dentro del workspace | Orden y espacio: `/` tiene poco disco libre. |

## 11. Riesgos

| ID | Riesgo | Impacto | Mitigación |
|---|---|---|---|
| R1 | RAM insuficiente con Blender y el motor juntos | Alto | Un modelo a la vez, `/free` tras cada etapa, INT8, worker después de liberar, medir en cada benchmark. |
| R2 | Hunyuan3D-Part no compila para `sm_120` | Alto | Spike temprano en M5; P3-SAM solo (segmentar sin completar) como alternativa. |
| R3 | Calidad inferior a la esperada | Medio | Benchmark desde M1; varias semillas, multivista; modelos intercambiables. |
| R4 | Steam actualiza Blender solo y cambia la API de `bpy` | Medio | Declarar la versión mínima en el manifiesto; tests del worker; revisar al actualizar. |
| R5 | El Blender de Steam no puede ejecutar `docker` (entorno de Steam) | Medio | Verificarlo al inicio de M3; alternativa: lanzar Blender fuera de Steam o usar un servicio auxiliar. |
| R6 | Cambios en la API o los nodos de ComfyUI | Medio | Versión fijada; actualizar solo con los tests de integración en verde. |
| R7 | Poco disco en `/` para Docker | Bajo | Imagen mínima (1 GB); todo lo demás en `engine/`. |
| R8 | Licencias de modelos | Bajo (uso personal) | Licencia visible en el gestor; revisar antes de cualquier uso comercial. |
| R9 | Modelos o nodos maliciosos | Medio | Orgs aprobadas, `.safetensors`, SHA-256, contenedor sin privilegios, nodos de terceros revisados. |

## 12. Preguntas abiertas

1. ¿Uso comercial a futuro (vender modelos o la herramienta)? Afecta las licencias.
2. ¿Qué modelo de texto → imagen usar? Se decide en M6.
3. ¿Fijar una versión de Blender o seguir la de Steam?
4. ¿Arreglar la red bridge de Docker (requiere `sudo`) o mantener la red del host?
