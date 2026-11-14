"""Interfaz de Blender. Único lugar (junto con worker/) donde se importa bpy."""

from __future__ import annotations

import atexit

import bpy

from . import ops, panels, prefs, previews, props, runtime

CLASSES = (*prefs.CLASSES, *props.CLASSES, *ops.CLASSES, *panels.CLASSES)
TICK_SECONDS = 0.5
_last_version = -1


def _tick() -> float:
    """Timer del hilo principal: callbacks de tareas terminadas y redibujado."""
    global _last_version
    try:
        rt = runtime.get()
    except RuntimeError:
        return TICK_SECONDS
    for task in rt.tasks.take_finished():
        if task.on_done is not None:
            try:
                task.on_done(task)
            except Exception as err:
                rt.last_error = f"{task.title}: {err}"
        rt.version += 1
    if rt.tasks.any_running() or rt.version != _last_version:
        _last_version = rt.version
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    return TICK_SECONDS


def _stop_engine_if_configured(rt: runtime.Runtime | None) -> None:
    """Al cerrar Blender (o deshabilitar el addon): detener el motor si así está configurado.

    Blender deshabilita los addons al salir, así que esto corre desde unregister(). La orden
    se lanza desacoplada para no demorar el cierre.
    """
    if rt is None:
        return
    try:
        preferences = prefs.get()
        if preferences is not None and not preferences.stop_engine_on_exit:
            return
        rt.manager.stop_detached()
    except Exception:
        pass


def _on_exit() -> None:
    """Respaldo por si Blender sale sin deshabilitar el addon."""
    _stop_engine_if_configured(runtime.shutdown())


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    props.register()
    preferences = prefs.get()
    runtime.init(preferences.workspace if preferences else "")
    bpy.app.timers.register(_tick, first_interval=TICK_SECONDS, persistent=True)
    atexit.register(_on_exit)


def unregister() -> None:
    atexit.unregister(_on_exit)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _stop_engine_if_configured(runtime.shutdown())
    previews.clear()
    props.unregister()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
