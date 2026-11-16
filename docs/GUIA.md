# Guía de uso — 3DAI Modeler

Convierte una imagen en un modelo 3D texturizado dentro de Blender, usando IA local (nada sale
de tu PC). Esta guía cubre la instalación, el uso del addon y qué hacer si algo falla.

## 1. Instalación (una sola vez)

Requisitos: Linux, GPU NVIDIA (16 GB de VRAM recomendados), Docker con
`nvidia-container-toolkit`, Blender 5.x.

```bash
./scripts/install-addon.sh        # con Blender cerrado
```

**Blender de Steam:** Steam ejecuta Blender dentro de su contenedor, que oculta Docker. En Steam →
Blender → Propiedades → Opciones de lanzamiento, pon:

```
PRESSURE_VESSEL_FILESYSTEMS_RW=/run/docker.sock %command%
```

Abre Blender, pulsa `N` en el Viewport 3D y entra en la pestaña **3DAI**. La primera vez:

1. **Motor → Instalar** (construye el motor y descarga ComfyUI y PyTorch, ~8 GB).
2. **Motor → Modelos en uso → Descargar** el modelo de generación (Pixal3D, ~10 GB la primera vez).

Todo se guarda dentro de la carpeta del proyecto (`engine/`).

## 2. Generar un modelo

En **Generar**:

| Opción | Qué hace |
|---|---|
| Imagen | La imagen del objeto (el **frente** si usas vistas adicionales) |
| Calidad | Borrador (rápido) · Estándar · Máxima (texturas 8K) · Personalizada |
| Polígonos | Baja 10k · Media 50k · Alta 200k · Ultra 700k · Personalizada · **Sin reducir** |
| Nombre | Nombre del objeto en la escena (vacío: el nombre de la imagen) |
| Separar en partes | Divide el modelo en piezas, cada una como objeto (P3-SAM) |
| Enderezar | Corrige la inclinación de fotos tomadas desde arriba |
| Proyectar imagen | Pinta tu imagen sobre el modelo: texturas con su nitidez en lo visible |

Pulsa **Generar modelo 3D**. El motor arranca solo si estaba detenido. Al terminar, el modelo
aparece en el cursor 3D dentro de una colección nueva. El panel muestra el progreso por etapa y
un tiempo estimado según tus trabajos anteriores.

### Vistas adicionales (opcional)

Si tienes el mismo objeto visto de **izquierda, espalda o derecha** (misma pose y escala que el
frente), cárgalas en **Vistas adicionales**. Se usa entonces Pixal3D multivista: la espalda y los
costados salen fieles a tu diseño en vez de inventados, y cada vista se proyecta sobre su lado.

### Calidad personalizada

Con **Personalizada** aparece **Avanzado**: pasos de la IA, resolución de detalle y remesh,
tamaño de cada textura (1K a 8K), semilla y exportación a **FBX/OBJ**. Usa "Partir de" para
copiar un preset y ajustar desde ahí.

## 3. Qué imagen da buenos resultados

- **Un solo objeto**, completo, sin nada delante.
- **Fondo liso** o simple.
- **De frente o 3/4**, a la altura del objeto.
- **Luz pareja**: las sombras fuertes y los brillos quedan pintados en la textura.
- **1024 px o más**, nítida.
- **Personajes:** pose A o T, brazos separados del cuerpo.
- Vidrio, transparencias y brillos (emisión) no se reproducen.

## 4. Motor y modelos

**Motor** muestra el estado, la VRAM y la RAM en uso, y los modelos en uso.

- **Detener** libera toda la RAM y la VRAM del motor. Al cerrar Blender se detiene solo
  (configurable en las preferencias del addon).
- **Gestionar modelos:** descargar, borrar, ver el espacio en disco y agregar modelos:
  - **Desde Hugging Face:** escribe el repositorio y elige el archivo.
  - **Desde archivo:** un `.safetensors` de tu PC. Se reconoce la arquitectura leyendo solo su
    cabecera; si es idéntico a un archivo oficial queda instalado sin volver a descargarlo.

Modelos de generación:

| Modelo | Uso |
|---|---|
| Pixal3D (INT8) | Predeterminado: fiel a la imagen, rápido, cabe en 16 GB |
| Pixal3D (bf16) | Un poco más preciso; más RAM y más lento |
| Pixal3D multivista | Se usa solo cuando cargas vistas adicionales |
| TRELLIS.2 (INT8) | "Interpreta" más la imagen; sin proyección de textura |

## 5. Estilos

**Estilos** cambia cómo se ve la escena, sin tocar los modelos: Anime / Cel shading (bandas de
sombra y contorno), Blanco y negro, Colores saturados y Acuarela, con intensidad ajustable.
**Quitar estilo** deja todo exactamente como estaba.

## 6. Resultados

**Resultados** lista los últimos trabajos con su miniatura: **Importar** de nuevo, **Reanudar** uno
interrumpido, abrir su carpeta o borrarlo. Cada trabajo vive en `engine/jobs/<id>/`:

| Archivo | Qué es |
|---|---|
| `postprocess/model.blend` | El resultado final para Blender |
| `generate/model.glb` | El modelo tal como sale de la IA (formato estándar para otros programas) |
| `postprocess/model.fbx`, `.obj` | Si pediste exportarlos |
| `job.json` | Parámetros, estado y métricas del trabajo |

## 7. Si algo falla

| Síntoma | Causa y solución |
|---|---|
| "No disponible: Blender abierto desde Steam no puede usar Docker" | Falta la opción de lanzamiento de Steam (sección 1) |
| El motor se detiene solo a mitad de una generación | El sistema lo mató por falta de RAM (`systemd-oomd`). Cierra programas pesados; opcional: `sudo systemctl edit docker.service` y agrega `[Service]` + `ManagedOOMPreference=avoid` |
| "out of memory" al hornear | La combinación de texturas no cabe en tu VRAM: baja el normal map a 4K en Personalizada |
| "el modelo … no está descargado" | Descárgalo en Motor → Gestionar modelos |
| Una generación se cortó | Resultados → **Reanudar**: continúa desde la última etapa terminada |
| Cualquier otro error | Motor → botón de logs: copia todo a un texto de Blender ("3DAI Modeler.log") |

## 8. Sin abrir Blender (línea de comandos)

```bash
./scripts/engine.sh up                                   # iniciar el motor
./scripts/dev.sh cli generate foto.png --quality max --faces high --parts
./scripts/dev.sh cli generate frente.png --back espalda.png   # multivista
./scripts/dev.sh cli generate foto.png --quality custom --base max --set texture_size=8192
./scripts/dev.sh cli jobs                                # listar trabajos
./scripts/dev.sh cli resume <id> [--from postprocess]    # reanudar o repetir una etapa
./scripts/dev.sh cli models list | download <id> | remove <id>
./scripts/engine.sh down                                 # detener el motor
```
