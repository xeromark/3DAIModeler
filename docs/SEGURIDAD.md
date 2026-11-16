# Seguridad — 3DAI Modeler

Revisión del 2026-11-16 (versión 1.0.0). Todo corre en el PC del usuario; el objetivo es que
ninguna imagen ni modelo salga del equipo y que el código de terceros quede acotado.

## Controles

| Área | Control | Verificado |
|---|---|---|
| Red | ComfyUI escucha solo en `127.0.0.1` y corre con `--offline` (sin nodos de pago en la nube; la interfaz web no se conecta a internet) | Puerto solo en loopback |
| Red | ComfyUI rechaza peticiones de otras páginas web (cabecera `Origin` ajena) | `POST /prompt` con `Origin: https://evil.example` → **403** |
| Contenedor | Usuario sin privilegios (tu UID), `cap_drop: ALL`, `no-new-privileges`, solo ve `engine/` y los scripts y nodos propios en **solo lectura** | `compose.yaml` |
| Modelos | Descargas desde Hugging Face con **revisión fijada** a un commit y **SHA-256** verificado; el archivo final solo aparece si el hash coincide | Tests de registro |
| Modelos | Solo `.safetensors` (no ejecutan código al cargarse); otro formato exige confirmación explícita en el catálogo | Tests de registro |
| Modelos | Variantes del usuario: organización no aprobada pide confirmación; repositorio y ruta validados **antes** de cualquier consulta (sin `..`, sin rutas absolutas) | Tests de registro |
| Archivos locales | Se lee solo la cabecera del `.safetensors` para reconocerlo; nunca se carga el contenido para validarlo | Tests de modelos locales |
| Rutas | IDs de trabajo validados (`AAAAMMDD-HHMMSS-xxxx`); borrar un trabajo exige que esté dentro de `engine/jobs/` y tenga `job.json`; destinos de modelos sin `..` | Tests de trabajos y registro |
| Procesos | Ningún `shell=True`, `os.system`, `eval`, `exec` ni `pickle` en el código del proyecto; todos los comandos se lanzan como listas de argumentos | Búsqueda en el código |
| Dependencias | Motor y entorno de partes con **lockfiles** (`requirements.lock`, `parts-requirements.lock`); ComfyUI y Hunyuan3D-Part fijados a versión/commit | `scripts/container/` |
| P3-SAM | `HF_HUB_OFFLINE=1`: el código de terceros no puede descargar nada; pesos cargados con `safetensors` (sin `torch.load`) | `parts_segment.py` |

## Riesgos que quedan (y por qué se aceptan)

1. **Acceso a Docker = acceso de administrador.** Estar en el grupo `docker` permite controlar el
   sistema. Es un requisito de Docker, no de la app; la app solo ejecuta `docker compose` sobre su
   propio `compose.yaml`.
2. **La opción de lanzamiento de Steam expone el socket de Docker** a todo lo que corra dentro del
   Blender de Steam (incluidos otros addons). Instala solo addons de confianza, o abre Blender
   fuera de Steam.
3. **Red del host:** el contenedor usa la red del PC (la red *bridge* de Docker no tenía salida a
   internet en este equipo). ComfyUI solo escucha en `127.0.0.1`, pero el contenedor podría
   conectarse a servicios locales. Si se arregla la red *bridge*, conviene volver a ella.
4. **Licencias:** Hunyuan3D-Part (P3-SAM) no es válida en la UE, el Reino Unido ni Corea del Sur.
   El addon lo avisa.
5. **Imagen base `ubuntu:24.04`** sin digest fijado: recibe actualizaciones de seguridad, a cambio
   de no ser 100 % reproducible.

## Recomendaciones para el usuario

- Mantener los modelos en organizaciones aprobadas (Comfy-Org, microsoft, tencent, TencentARC).
- No agregar nodos de ComfyUI de terceros sin revisar su código.
- Si `systemd-oomd` detiene el motor con frecuencia: `ManagedOOMPreference=avoid` en
  `docker.service` (ver la guía).
