"""
ComfyUI-Hermes-Listener
======================
Intercepta eventos nativos de ejecución (inicio, fin, error), libera VRAM tras
cada proceso y escribe señales locales para despertar al agente. Zero-overhead:
opera en segundo plano sin nodos en el canvas.

No requiere dependencias externas ni servicios terceros. Autónomo.
"""

import os
import asyncio
from aiohttp import web
import server as comfy_server
from .nodes.comfy_hermes import init_listener

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
WEB_DIRECTORY = "./web"

# ─── INSTANCIA GLOBAL ──────────────────────────────────
listener = init_listener(CONFIG_PATH)


# ─── OBTener referencia al servidor de ComfyUI ─────────
def _get_server():
    """Obtiene la instancia correcta del servidor según la versión de ComfyUI."""
    # 0.27+: PromptServer es una clase, no tiene .instance
    # < 0.27: PromptServer.instance existe
    if hasattr(comfy_server.PromptServer, 'instance'):
        return comfy_server.PromptServer.instance
    return comfy_server.PromptServer


_server = _get_server()


# ─── OBTener el router de aiohttp ──────────────────────
def _get_router():
    """Obtiene el router de aiohttp para registrar rutas."""
    if hasattr(_server, 'app') and hasattr(_server.app, 'router'):
        return _server.app.router
    if hasattr(_server, 'router'):
        return _server.router
    return None


_router = _get_router()


# ─── REGISTRO DE EVENTOS PARA COMFYUI 0.27.0+ ──────
try:
    if hasattr(_server, 'add_on_prompt_handler'):
        # ComfyUI 0.27+: API oficial add_on_prompt_handler
        def _on_prompt_handler(event, data, sid=None):
            try:
                listener.on_event(event, data)
                # Debug webhook
                import json
                webhook_data = json.dumps({"event": event, "status": "ok" if event in ["execution_start", "prompt_completed", "execution_error"] else "other"})
                print(f"[Hermes-Listener] Webhook enviado a hermes: {webhook_data}")
            except Exception:
                import traceback
                print(f"[Hermes-Listener] Error en evento {event}: {traceback.format_exc()}")
        _server.add_on_prompt_handler(_on_prompt_handler)
        print("[Hermes-Listener] Usando add_on_prompt_handler (ComfyUI 0.27+)")
    else:
        # Monkey-patch de send_sync
        _original_send_sync = _server.send_sync
        def _patched_send_sync(event, data, sid=None):
            try:
                listener.on_event(event, data)
            except Exception:
                pass
            return _original_send_sync(event, data, sid)
        _server.send_sync = _patched_send_sync
        print("[Hermes-Listener] Usando monkey-patch send_sync")
except Exception as e:
    print(f"[Hermes-Listener] Error registrando eventos: {e}")


# ─── ROUTES ─────────────────────────────────────────────

def _register_routes():
    """Registra las rutas API en el router de aiohttp."""
    if _router is None:
        print("[Hermes-Listener] AVISO: no se encontró router de aiohttp")
        return

    try:
        # POST /comfy_hermes/update_config
        _router.add_post('/comfy_hermes/update_config', api_update_config)
        print("[Hermes-Listener] Ruta registrada: /comfy_hermes/update_config")

        # GET /comfy_hermes/status
        _router.add_get('/comfy_hermes/status', api_get_status)
        print("[Hermes-Listener] Ruta registrada: /comfy_hermes/status")

        # POST /comfy_hermes/free_vram
        _router.add_post('/comfy_hermes/free_vram', api_free_vram)
        print("[Hermes-Listener] Ruta registrada: /comfy_hermes/free_vram")
    except Exception as e:
        print(f"[Hermes-Listener] Error registrando rutas: {e}")


async def api_update_config(request):
    try:
        payload = await request.json()
        await asyncio.to_thread(listener.update_config, payload)
        return web.json_response({"status": "ok"})
    except Exception as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=500)


async def api_get_status(request):
    info = {
        "enabled": listener.enabled,
        "events": dict(listener.events),
        "do_vram_cleanup": listener.do_vram_cleanup,
        "signal_file": listener.signal_file,
        "last_prompt_id": listener.last_prompt_id,
        "last_event": listener.last_event,
    }
    return web.json_response(info)


async def api_free_vram(request):
    try:
        result = await asyncio.to_thread(listener.manual_vram_cleanup)
        return web.json_response({"status": "ok", **result})
    except Exception as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=500)


# Registrar rutas inmediatamente
_register_routes()


# ─── Sin nodos en el canvas (operacion invisible) ────────
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
