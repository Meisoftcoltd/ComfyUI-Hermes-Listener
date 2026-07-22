#!/usr/bin/env python3
import json, os, sys, threading, time, urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# CONFIG
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SIGNAL_FILE = os.path.join(BASE_DIR, "signal_hermes.json")
COMFYUI_URL = "http://127.0.0.1:8189"
TG_BOT = "Elarabot"
TG_CHAT = "meisoft"

def log(msg):
    print(f"{datetime.now().strftime('%H:%M:%S')} [INFO] {msg}")

def send_tg_report(text, caption=""):
    """Sends a formatted Markdown report to Telegram via ComfyUI."""
    try:
        wf = {"prompt": {
            "1": {"class_type": "TelegramSuite_TelegramBot", 
                 "inputs": {"bot": TG_BOT, "chat": TG_CHAT, "api_url": "https://api.telegram.org"}},
            "2": {"class_type": "TelegramSuite_SendMessage", 
                 "inputs": {
                     "bot": ["1", 0], "chat_id": [TG_CHAT, 1], "text": text, 
                     "caption": caption, "parse_mode": "Markdown"
                 }}
        }}
        req = urllib.request.Request(f"{COMFYUI_URL}/prompt", 
                                     data=json.dumps(wf).encode(), 
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"  [TG Error] {e}")

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        body = self.rfile.read(content_length)
        data = json.loads(body.decode('utf-8'))
        
        # Extract info for logging
        event = ""
        node_id = ""
        if isinstance(data, dict):
            val = data.get("value", {})
            if isinstance(val, dict):
                event = val.get("event", "")
                node_id = val.get("node", "")
            else:
                event = str(val)
        
        log(f"EVENT DETECTED: '{event}' | Node: '{node_id}'")

        if event == "failed":
            # Send a BEAUTIFUL error message to Telegram
            msg = f"⚠️ **ERROR EN COMPFYUI**\n`Event: {event}`\n`Node: {node_id}`\n---\n`Details: {str(data)[:200]}`"
            send_tg_report(msg, caption="System Report")

        self.send_response(200); self.end_headers(); self.wfile.write(b'{"status":"ok"}')

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"status":"running"}')
        else:
            self.send_response(404); self.end_headers()

if __name__ == "__main__":
    print("[INFO] Server ready on port 9119")
    HTTPServer(('127.0.0.1', 9119), WebhookHandler).serve_forever()
