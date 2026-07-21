#!/usr/bin/env python3
"""
ComfyUI Generation API — simple JSON interface for generating images.

USAGE (curl):
  curl -X POST http://127.0.0.1:9119/api/comfyui-gen \
    -H "Content-Type: application/json" \
    -d '{"prompt":"cute chibi cat","width":1024,"height":1024}'

CLI (direct):
  python3 comfyui_gen.py --prompt "cute cat" --width 1024 --height 1024

HTTP SERVER:
  python3 comfyui_gen.py --serve --port 9119

RESPONSE (after completion):
  {"status": "success|error", "task_id": "...", "prompt": "...", "message": "..."}
  - If telegram enabled, image is sent to Telegram automatically.
  - Log at /home/meisoft/.hermes/comfyui_gen.log
"""

import json
import sys
import time
import uuid
import logging
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────
LOG_PATH = Path("/home/meisoft/.hermes/comfyui_gen.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_PATH)),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

COMFYUI_URL = "http://127.0.0.1:8189"
WEBHOOK_STATUS_URL = "http://127.0.0.1:9119/api/comfyui/webhook/status"
TEMPLATE_PATH = Path("/home/meisoft/ComfyUI-Hermes-Listener/templates/base_flux2.json")

# ── Defaults ───────────────────────────────────────────────────────────────
DEFAULTS = {
    "prompt": "chibi cat anime 2000s style",
    "negative_prompt": "low quality, blurry, watermark, ugly",
    "width": 1024,
    "height": 1024,
    "steps": 30,
    "cfg": 1.0,
    "seed": -1,
    "clip": "qwen_3_8b_fp8mixed.safetensors",
    "clip_type": "flux2",
    "unet": "flux-2-klein-9b.safetensors",
    "unet_dtype": "fp8_e4m3fn",
    "vae": "flux2-vae.safetensors",
    "sampler": "euler",
    "scheduler": "simple",
    "denoise": 1.0,
    "filename_prefix": "ComfyUI",
    "telegram": {
        "enabled": True,
        "bot": "Elarabot",
        "chat": "meisoft",
        "parse_mode": "HTML",
        "format": "png",
        "show_caption_above_media": False,
    },
}


# ── Template Loading ──────────────────────────────────────────────────────

def load_template():
    """Load the base workflow template."""
    with open(TEMPLATE_PATH, "r") as f:
        return json.load(f)


# ── Workflow Builder ──────────────────────────────────────────────────────

def build_workflow(params):
    """Build a complete workflow from params, replacing placeholders."""
    wf = load_template()
    p = dict(DEFAULTS)
    for k, v in params.items():
        if isinstance(v, dict) and k in p and isinstance(p[k], dict):
            p[k].update(v)
        else:
            p[k] = v

    # Extract values
    prompt = p.get("prompt", p["prompt"])
    neg = p.get("negative_prompt", "")
    width = int(p.get("width", 1024))
    height = int(p.get("height", 1024))
    steps = int(p.get("steps", 30))
    cfg = float(p.get("cfg", 1.0))
    seed = p.get("seed", -1)
    sampler = p.get("sampler", "euler")
    scheduler = p.get("scheduler", "simple")
    denoise = float(p.get("denoise", 1.0))
    clip_name = p.get("clip", "qwen_3_8b_fp8mixed.safetensors")
    clip_type = p.get("clip_type", "flux2")
    unet = p.get("unet", "flux-2-klein-9b.safetensors")
    unet_dtype = p.get("unet_dtype", "fp8_e4m3fn")
    vae = p.get("vae", "flux2-vae.safetensors")
    prefix = p.get("filename_prefix", "ComfyUI")

    tg = p.get("telegram", {})
    tg_enabled = tg.get("enabled", True)
    bot = tg.get("bot", "Elarabot")
    chat_id = tg.get("chat", "meisoft")
    parse_mode = tg.get("parse_mode", "HTML")
    img_fmt = tg.get("format", "png").upper()
    show_caption = tg.get("show_caption_above_media", False)

    # Telegram caption
    tg_caption = prompt

    # Build prompt text
    full_prompt = prompt

    # ── Replace placeholders ─────────────────────────────────────────────
    for nid, nd in wf.items():
        inp = nd.get("inputs", {})
        ct = nd.get("class_type", "")

        # CLIP name
        if ct == "CLIPLoader":
            inp["clip_name"] = clip_name
            inp["type"] = clip_type

        # UNet
        if ct == "UNETLoader":
            inp["unet_name"] = unet
            inp["weight_dtype"] = unet_dtype

        # VAE
        if ct == "VAELoader":
            inp["vae_name"] = vae

        # Prompt text (CLIPTextEncode)
        if ct == "CLIPTextEncode":
            if inp.get("text") == "PROMPT_PLACEHOLDER":
                inp["text"] = full_prompt
            elif inp.get("text") == "NEGATIVE_PROMPT_PLACEHOLDER":
                inp["text"] = neg

        # Latent size
        if ct == "EmptyLatentImage":
            inp["width"] = width
            inp["height"] = height

        # KSampler params
        if ct == "KSampler":
            inp["sampler_name"] = sampler
            inp["scheduler"] = scheduler
            inp["steps"] = steps
            inp["cfg"] = cfg
            inp["denoise"] = denoise
            if seed == -1:
                inp["seed"] = int(time.time()) % 1000000000  # random-ish
            else:
                inp["seed"] = int(seed)

        # SaveImage
        if ct == "SaveImage":
            inp["filename_prefix"] = prefix

        # Telegram bot setup
        if ct == "TelegramSuite_TelegramBot":
            inp["bot"] = bot
            inp["chat_id"] = chat_id

        # Telegram SendImage
        if ct == "TelegramSuite_SendImage":
            # IMAGE field - connect to SaveImage
            if inp.get("IMAGE") == "IMG_PLACEHOLDER":
                inp["IMAGE"] = ["12", 0]
            inp["parse_mode"] = parse_mode
            inp["format"] = img_fmt
            inp["show_caption_above_media"] = show_caption
            if inp.get("caption") == "CAPTION_PLACEHOLDER":
                inp["caption"] = tg_caption

    # If telegram disabled, remove telegram nodes
    if not tg_enabled:
        for nid in list(wf.keys()):
            nd = wf[nid]
            ct = nd.get("class_type", "")
            if ct in ("TelegramSuite_TelegramBot", "TelegramSuite_SendImage", "TelegramSuite_SendMessage"):
                del wf[nid]

    return wf


# ── ComfyUI Communication ─────────────────────────────────────────────────

def send_to_comfyui(workflow):
    """Send workflow to ComfyUI /prompt endpoint."""
    import urllib.request
    import urllib.error

    url = f"{COMFYUI_URL}/prompt"
    data = json.dumps({"prompt": workflow}).encode("utf-8")

    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        raise Exception(f"ComfyUI HTTP error: {e.code} - {error_body}")
    except urllib.error.URLError as e:
        raise Exception(f"ComfyUI connection error: {e}")


def wait_for_completion(prompt_id, timeout=180):
    """Wait for ComfyUI to finish via /history endpoint."""
    import urllib.request

    start = time.time()
    while time.time() - start < timeout:
        try:
            url = f"{COMFYUI_URL}/history/{prompt_id}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read().decode())
                if prompt_id in result:
                    status_data = result[prompt_id]
                    status = status_data.get("status", {}).get("status_str", "unknown")
                    if status == "success":
                        return "success", "Image generated and sent to Telegram"
                    elif status == "error":
                        errors = status_data.get("node_errors", {})
                        err_msg = "; ".join(str(v) for v in errors.values())
                        return "error", err_msg
                    elif status == "running" or status == "pending":
                        time.sleep(3)
                        continue
                else:
                    time.sleep(3)
        except urllib.error.HTTPError:
            # Not found yet
            time.sleep(3)
        except Exception as e:
            logger.warning(f"History check: {e}")
            time.sleep(3)

    return "timeout", f"Timed out after {timeout}s"

# ── Main Handler ──────────────────────────────────────────────────────────

def handle_generation(params):
    """Build → send → wait → return result."""
    task_id = str(uuid.uuid4())

    try:
        wf = build_workflow(params)
        result = send_to_comfyui(wf)
        prompt_id = result.get("prompt_id", "")

        logger.info(f"Task {task_id}: sent prompt_id={prompt_id}, prompt={params.get('prompt','')[:80]}")
        # Step 3: Wait for completion
        status, msg = wait_for_completion(prompt_id, timeout=180)

        logger.info(f"Task {task_id} done: status={status}")
        return {
            "status": status,
            "task_id": task_id,
            "prompt_id": prompt_id,
            "prompt": params.get("prompt", ""),
            "message": msg,
        }

    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}")
        return {
            "status": "error",
            "task_id": task_id,
            "prompt": params.get("prompt", ""),
            "message": str(e),
        }


# ── HTTP Server ────────────────────────────────────────────────────────────

def start_server(port=9119):
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path == "/api/comfyui-gen":
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                params = json.loads(body) if body else {}
                result = handle_generation(params)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result, indent=2).encode())
            else:
                self.send_response(404)
                self.end_headers()

        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, fmt, *args):
            logger.info(f"[HTTP] {fmt % args}")

    server = HTTPServer(("0.0.0.0", port), Handler)
    logger.info(f"Server on :{port} — POST /api/comfyui-gen")
    logger.info(f"Health: GET http://127.0.0.1:{port}/health")
    server.serve_forever()


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="ComfyUI gen — simple image generation")
    ap.add_argument("--prompt", default="chibi cat anime 2000s style")
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=-1)
    ap.add_argument("--neg", default="low quality, blurry, watermark, ugly", help="Negative prompt")
    ap.add_argument("--sampler", default="euler")
    ap.add_argument("--scheduler", default="simple")
    ap.add_argument("--denoise", type=float, default=1.0)
    ap.add_argument("--unet", default="flux-2-klein-9b.safetensors")
    ap.add_argument("--vae", default="flux2-vae.safetensors")
    ap.add_argument("--telegram", action="store_true", help="Send to Telegram")
    ap.add_argument("--no-telegram", action="store_true", help="Skip Telegram")
    ap.add_argument("--bot", default="Elarabot")
    ap.add_argument("--chat", default="meisoft")
    ap.add_argument("--serve", action="store_true", help="Start HTTP server")
    ap.add_argument("--port", type=int, default=9119)
    ap.add_argument("--dry-run", action="store_true", help="Build workflow and print JSON only")

    args = ap.parse_args()

    # Build params
    params = {
        "prompt": args.prompt,
        "negative_prompt": args.neg,
        "width": args.width,
        "height": args.height,
        "steps": args.steps,
        "cfg": args.cfg,
        "seed": args.seed,
        "sampler": args.sampler,
        "scheduler": args.scheduler,
        "denoise": args.denoise,
        "unet": args.unet,
        "vae": args.vae,
        "telegram": {
            "enabled": args.telegram,
            "bot": args.bot,
            "chat": args.chat,
        },
    }

    if args.no_telegram:
        params["telegram"]["enabled"] = False

    if args.dry_run:
        wf = build_workflow(params)
        print(json.dumps(wf, indent=2))
    elif args.serve:
        start_server(args.port)
    else:
        result = handle_generation(params)
        print(json.dumps(result, indent=2))