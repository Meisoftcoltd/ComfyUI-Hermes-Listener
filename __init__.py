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
    """Obtiene la instancia correcta del servidor segun la version de ComfyUI."""
    # 0.27+: PromptServer es una clase, no tiene .instance
    # < 0.27: PromptServer.instance existe
    if hasattr(comfy_server.PromptServer, 'instance'):
        return comfy_server.PromptServer.instance
    return comfy_server.PromptServer


_server = _get_server()


# ─── REGISTRO DE EVENTOS PARA COMFYUI 0.27.0+ ──────
try:
    if hasattr(_server, 'add_on_prompt_handler'):
        # ComfyUI 0.27+: API oficial add_on_prompt_handler
        def _on_prompt_handler(event, data, sid=None):
            try:
                listener.on_event(event, data)
            except Exception:
                pass
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

# Detectar si las routes existen en el servidor
_routes = getattr(_server, 'routes', None)
if _routes is None:
    # 0.27+: routes puede estar como decorator en la clase
    _routes = _server


@(_routes.post if hasattr(_routes, 'post') else lambda x: x)("comfy_hermes/update_config")
async def api_update_config(request):
    try:
        payload = await request.json()
        await asyncio.to_thread(listener.update_config, payload)
        return web.json_response({"status": "ok"})
    except Exception as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=500)


@(_routes.get if hasattr(_routes, 'get') else lambda x: x)("comfy_hermes/status")
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


@(_routes.post if hasattr(_routes, 'post') else lambda x: x)("comfy_hermes/free_vram")
async def api_free_vram(request):
    try:
        result = await asyncio.to_thread(listener.manual_vram_cleanup)
        return web.json_response({"status": "ok", **result})
    except Exception as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=500)


# ─── Sin nodos en el canvas (operacion invisible) ────────
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
