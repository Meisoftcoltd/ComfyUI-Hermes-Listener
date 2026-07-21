#!/usr/bin/env python3
"""
webhook_server.py — Receptor de webhooks de ComfyUI en puerto 9119.

Recibe eventos de ejecución de ComfyUI (inicio, fin, error, progreso)
y los persiste en signal_hermes.json para que el agente pueda consultarlos
sin hacer sleep fijo ni polling excesivo.

Uso:
    python3 webhook_server.py

Endpoint: POST /api/comfyui/webhook
Response: {"status": "ok", "event": "<tipo>", "prompt_id": "<id>"}
"""

import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
import threading

SIGNAL_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "signal_hermes.json"
)

# Directorio alternativo donde ComfyUI-Hermes-Listener ya escribe
ALTERNATE_SIGNAL = "/home/meisoft/ComfyUI/custom_nodes/ComfyUI-Hermes-Listener/signal_hermes.json"

last_state = {}
state_lock = threading.Lock()


def save_signal(payload: dict) -> None:
    """Persiste el estado en el archivo de señal."""
    payload["received_at"] = datetime.now(timezone.utc).isoformat()
    tmp = SIGNAL_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SIGNAL_FILE)
    except Exception:
        pass
    # También en el directorio del listener original
    try:
        alt = ALTERNATE_SIGNAL
        tmp2 = alt + ".tmp"
        with open(tmp2, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, encoding="utf-8")
        os.replace(tmp2, alt)
    except Exception:
        pass

    with state_lock:
        last_state.clear()
        last_state.update(payload)


class WebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/comfyui/webhook/status" or self.path == "/status":
            with state_lock:
                resp = {
                    "status": "ok",
                    "last_signal": dict(last_state) if last_state else None,
                    "signal_file": SIGNAL_FILE,
                }
            body = json.dumps(resp, indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health" or self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = json.dumps({"status": "running", "port": 9119}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/comfyui/webhook":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                resp = {"status": "error", "message": "invalid JSON"}
                encoded = json.dumps(resp).encode()
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return

            save_signal(payload)

            resp = {
                "status": "ok",
                "event": payload.get("estado") or payload.get("event"),
                "prompt_id": payload.get("prompt_id"),
            }
            encoded = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            print(f"[webhook] Recibido: estado={resp['event']} prompt={payload.get('prompt_id')}")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default stderr logging."""
        print(f"[webhook] {args[0] if args else ''}")


def main():
    port = 9119
    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    print(f"[webhook] Escuchando en :{port}")
    print(f"[webhook] POST /api/comfyui/webhook — guardar estado")
    print(f"[webhook] GET  /api/comfyui/webhook/status — leer último estado")
    print(f"[webhook] GET  /health — health check")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[webhook] Detenido")
        server.shutdown()


if __name__ == "__main__":
    main()
