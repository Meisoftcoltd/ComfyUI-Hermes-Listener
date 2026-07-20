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

# ─── Instancia global del listener ──────────────────────
listener = init_listener(CONFIG_PATH)


# ─── PATCH: Captura de eventos nativos via monkey-patch ─────
_original_send_sync = comfy_server.PromptServer.instance.send_sync


def _patched_send_sync(event, data, sid=None):
    """Wrapper que intercepta eventos antes y después de que ComfyUI los procese."""
    try:
        listener.on_event(event, data)
    except Exception:
        pass  # No romper el flujo de ComfyUI por error propio
    return _original_send_sync(event, data, sid)


comfy_server.PromptServer.instance.send_sync = _patched_send_sync


# ─── ROUTES ─────────────────────────────────────────────

@comfy_server.PromptServer.instance.routes.post("/comfy_hermes/update_config")
async def api_update_config(request):
    try:
        payload = await request.json()
        await asyncio.to_thread(listener.update_config, payload)
        return web.json_response({"status": "ok"})
    except Exception as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=500)


@comfy_server.PromptServer.instance.routes.get("/comfy_hermes/status")
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


@comfy_server.PromptServer.instance.routes.post("/comfy_hermes/free_vram")
async def api_free_vram(request):
    try:
        result = await asyncio.to_thread(listener.manual_vram_cleanup)
        return web.json_response({"status": "ok", **result})
    except Exception as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=500)


# ─── Sin nodos en el canvas (operación invisible) ────────
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
