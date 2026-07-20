"""
comfy_hermes.py — Motor de captura de eventos, señalización y VRAM cleanup.
Intercepta eventos nativos de ComfyUI (inicio, fin, error), libera VRAM
automáticamente tras cada ejecución y escribe archivos de señal para despertar al agente externo. Zero dependencias externas — 100 % autónomo.

License: MIT — David Martín / Meisoftcoltd
"""

import gc
import json
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger("ComfyUI-Hermes-Listener")

# ─────────── Eventos que ComfyUI emite ───────────
AVAILABLE_EVENTS = {
    "execution_start":   "Primera pulsación: empieza la ejecución del prompt",
    "prompt_completed":  "Pulsación executing con node=None → fin exitoso",
    "execution_error":   "Algo falló (nodo, excepción, traceback)",
    "progress_update":   "Barra de progreso en cada paso (muchos eventos)",
    "vram_cleanup_done": "VRAM liberada tras proceso (si cleanup activo)",
}

DEFAULT_EVENTS = {
    "execution_start":   True,
    "prompt_completed":  True,
    "execution_error":   True,
    "progress_update":   False,   # spammy — cada paso KSampler dispara uno
    "vram_cleanup_done": True,
}


# ═══════════ Core Listener ═══════════

class HermesListener:
    """Intercepta eventos de ejecución y los retransmite como señales."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.enabled: bool = True
        self.events: dict = dict(DEFAULT_EVENTS)
        self.do_vram_cleanup: bool = True
        self.signal_dir: str = os.path.dirname(config_path)
        self.signal_file: str = os.path.join(self.signal_dir, "signal_hermes.json")

        # Estado mutable protegido por lock
        self._lock = threading.Lock()
        self.last_prompt_id: str | None = None
        self.last_event: dict | None = None
        self.vram_before: float = 0.0
        self.vram_after: float = 0.0

    # ──────── Configuración ─────────────────────────────────

    def load_config(self) -> None:
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("enabled") is not None:
                self.enabled = bool(cfg["enabled"])
            for key in AVAILABLE_EVENTS:
                if key in cfg:
                    self.events[key] = bool(cfg[key])
            if "do_vram_cleanup" in cfg:
                self.do_vram_cleanup = bool(cfg["do_vram_cleanup"])
        except FileNotFoundError:
            logger.info("No config.json ─ usando valores por defecto")
        except Exception:
            logger.exception("Error cargando config")

    def save_config(self) -> None:
        try:
            cfg = {
                "enabled": self.enabled,
                "do_vram_cleanup": self.do_vram_cleanup,
                **self.events,
            }
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            logger.exception("Error guardando config")

    def update_config(self, payload: dict) -> None:
        if "enabled" in payload:
            self.enabled = bool(payload["enabled"])
        if "do_vram_cleanup" in payload:
            self.do_vram_cleanup = bool(payload["do_vram_cleanup"])
        for key in AVAILABLE_EVENTS:
            if key in payload:
                self.events[key] = bool(payload[key])
        self.save_config()

    # ──────── VRAM Cleanup (como SequentialBatcher) ─────────

    @staticmethod
    def _get_vram_used_gb() -> float:
        try:
            import torch
            if torch.cuda.is_available():
                return torch.cuda.memory_allocated() / 1e9
        except Exception:
            pass
        return 0.0

    def free_vram(self, prompt_id: str | None = None) -> dict:
        """Liberar toda la VRAM y devolver stats."""
        gb_before = self._get_vram_used_gb()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                gc.collect()
        except Exception as exc:
            logger.warning("VRAM cleanup parcialmente fallido: %s", exc)

        gb_after = self._get_vram_used_gb()
        freed = round(max(0, gb_before - gb_after), 2)
        stats = {
            "vram_before_gb": round(gb_before, 2),
            "vram_after_gb":  round(gb_after, 2),
            "vram_freed_gb":  freed,
        }

        with self._lock:
            self.vram_before = gb_before
            self.vram_after  = gb_after

        logger.info(
            "VRAM: %.2f → %.2f GB (liberó %.2f) [prompt=%s]",
            gb_before, gb_after, freed, prompt_id or "?",
        )
        return stats

    def manual_vram_cleanup(self) -> dict:
        """Liberar VRAM desde API externa manualmente."""
        return self.free_vram("manual")

    # ──────── Captura de eventos ───────────────────────────

    def on_event(self, event_type: str, data: dict | None = None) -> None:
        """Entry point: llamado desde el monkey-patch en cada evento de ComfyUI."""
        if not self.enabled:
            return

        data = data or {}
        normalized = self._normalize(event_type, data)
        if not normalized:
            return  # Evento desactivado → silencio total

        prompt_id = normalized.get("prompt_id")

        with self._lock:
            # ── Registrar inicio ────────────────────────────────
            if event_type == "execution_start":
                self.last_prompt_id = prompt_id
                gb = self._get_vram_used_gb()
                normalized["vram_at_start_gb"] = round(gb, 2)

            # ── Fin exitoso (executing + node=None) ─────────
            if event_type == "executing" and data.get("node") is None:
                normalized["estado"] = "fin"

            # ── Error ────────────────────────────────────────
            if event_type == "execution_error":
                normalized["estado"] = "error"

        # VRAM cleanup FUERA del lock para evitar deadlock con API concurrente
        sec_payload: dict | None = None
        if (event_type == "executing"
                and data.get("node") is None
                and self.do_vram_cleanup):
            vstats = self.free_vram(prompt_id)
            normalized.update(vstats)
            sec_payload = self._normalize(
                "vram_cleanup_done",
                {"prompt_id": prompt_id, **vstats},
            )

        # Inyectar metadatos originales
        normalized.setdefault("comfy_event", event_type)

        self._store_and_signal(normalized, prompt_id)

        if sec_payload:
            self._store_and_signal(sec_payload, prompt_id)

    def _normalize(self, evt: str, data: dict) -> dict | None:
        """Mapear evento raw → payload. Retorna None si desactivado."""
        pid = data.get("prompt_id") or self.last_prompt_id
        now = datetime.now(timezone.utc).isoformat()

        if evt == "execution_start" and self.events.get("execution_start"):
            return {"estado": "inicio", "timestamp": now, "prompt_id": pid}

        if evt == "executing":
            node = data.get("node")
            if node is None and self.events.get("prompt_completed"):
                return {"estado": "fin", "timestamp": now, "prompt_id": pid}

        if evt == "progress" and self.events.get("progress_update"):
            return {
                "estado": "progress",
                "timestamp": now,
                "value": data.get("value"),
                "max":   data.get("max"),
                "prompt_id": pid,
            }

        if evt == "execution_error" and self.events.get("execution_error"):
            return {
                "estado":             "error",
                "timestamp":          now,
                "prompt_id":          pid,
                "node_type":          data.get("node_type"),
                "node_id":            data.get("node_id"),
                "exception_type":     data.get("exception_type"),
                "exception_message":  data.get("exception_message"),
                "traceback":          data.get("traceback"),
            }

        if evt == "vram_cleanup_done" and self.events.get("vram_cleanup_done"):
            return {
                "estado":             "vram_freed",
                "timestamp":          now,
                "prompt_id":          pid or data.get("prompt_id"),
                "vram_before_gb":   data.get("vram_before_gb")    if "vram_before_gb" in data else None,
                "vram_after_gb":    data.get("vram_after_gb")     if "vram_after_gb"  in data else None,
                "vram_freed_gb":    data.get("vram_freed_gb")     if "vram_freed_gb"  in data else None,
            }

        return None

    # ──────── Señalización local ───────────────────────────

    def _store_and_signal(self, payload: dict, prompt_id: str | None) -> None:
        with self._lock:
            self.last_event = dict(payload)
        self._write_signal_file(payload)

    def _write_signal_file(self, payload: dict) -> None:
        tmp = self.signal_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.signal_file)
        except Exception:
            logger.exception("Error escribiendo señal a %s", self.signal_file)


# ─────────── Helpers de inicialización ──────────────────────

_instance: HermesListener | None = None


def init_listener(config_path: str) -> HermesListener:
    global _instance
    if _instance is None:
        _instance = HermesListener(config_path)
        _instance.load_config()
    return _instance


def get_listener() -> HermesListener:
    if _instance is None:
        raise RuntimeError("HermesListener no inicializado")
    return _instance
