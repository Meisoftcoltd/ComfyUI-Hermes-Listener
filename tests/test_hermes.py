"""
Tests para HermesListener sin ComfyUI server running. Solo valida logica interna.
Requiere: pytest installed in the custom_nodes env.
"""

import json, os, tempfile
from nodes.comfy_hermes import AVAILABLE_EVENTS, DEFAULT_EVENTS, HermesListener


def test_keys_match():
    for k in AVAILABLE_EVENTS:
        assert k in DEFAULT_EVENTS

def test_load_save(tmp_path):
    f = str(tmp_path / "config.json")
    lsn = HermesListener(f)
    lsn.events["progress_update"] = True
    lsn.do_vram_cleanup = False
    lsn.save_config()
    lsn2 = HermesListener(f)
    lsn2.load_config()
    assert lsn2.events["progress_update"] is True

def test_vram_stats():
    f = str(tempfile.mktemp(suffix=".json"))
    try:
        r = HermesListener(f).manual_vram_cleanup()
        assert "vram_before_gb" in r and "vram_freed_gb" in r
    finally:
        if os.path.exists(f):
            os.remove(f)
