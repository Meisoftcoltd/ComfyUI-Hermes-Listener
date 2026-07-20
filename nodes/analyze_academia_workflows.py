#!/usr/bin/env python3
"""
analyze_academia_workflows.py — Analiza todos los workflows de AcademiaSD/Flux2
y los alimenta en la base de datos de comfyui_nodes.

Categorías detectadas:
  - t2i_basic: Texto a imagen (básico, sin referencias)
  - t2i_multilora: Texto a imagen con múltiples LoRAs
  - i2i_basic: Imagen a imagen (1 referencia)
  - i2i_multilora: Imagen a imagen + múltiples LoRAs
  - flux2_klein_gguf: Flux2 Klein GGUF (v20-v30)
  - flux2_piflow: Pi-Flow GGUF
  - flux2_gguf: Flux2 GGUF base
  - flux_kontext: Flux Kontext (2+ referencias)

Cada categoría se guarda con:
  - nodos únicos y sus parámetros
  - conexiones mapeadas
  - tipo de uso (t2i, i2i, etc.)
  - nodos de AcademiaSD necesarios
  - nodos opcionales (upscaler, scheduler, etc.)
"""

import sqlite3
import json
import os
import sys
from datetime import datetime

DB_PATH = "/home/meisoft/ComfyUI-Hermes-Listener/comfyui_nodes.db"
WORLD_DIR = "/home/meisoft/comfyui_AcademiaSD/example_workflows"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def extract_node_info(node, wf, all_nodes):
    """Extrae info detallada de un nodo y sus conexiones."""
    ntype = node.get("type", "")
    props = node.get("properties", {})
    cnr_id = props.get("cnr_id", "")
    inputs = node.get("inputs", [])
    outputs = node.get("outputs", [])
    widgets = node.get("widgets_values", [])
    
    node_info = {
        "type": ntype,
        "cnr_id": cnr_id,
        "inputs": [],
        "outputs": [],
        "widgets": widgets,
        "is_custom": bool(cnr_id),
        "is_academia": "Academia" in ntype or "academia" in ntype.lower(),
        "is_essential": False,
    }
    
    # Procesar inputs
    for inp in inputs:
        if isinstance(inp, dict):
            link_id = inp.get("link")
            link_info = None
            if link_id and link_id in all_nodes.get("links", {}):
                link_info = all_nodes["links"][link_id]
            
            node_info["inputs"].append({
                "name": inp.get("name", ""),
                "type": inp.get("type", "*"),
                "link": link_id,
                "optional": inp.get("optional", False),
                "source_link": link_info,
            })
    
    # Procesar outputs
    for out in outputs:
        if isinstance(out, dict):
            links = [l.get("id") for l in out.get("links", [])]
            node_info["outputs"].append({
                "name": out.get("name", ""),
                "type": out.get("type", "*"),
                "links": links,
            })
    
    return node_info


def classify_workflow(fname, wf):
    """Clasifica un workflow según sus nodos y estructura."""
    types = set()
    for node in wf.get("nodes", []):
        ntype = node.get("type", "")
        if ntype:
            types.add(ntype)
    
    has_t2i = False
    has_i2i = False
    has_multilora = False
    has_upscale = False
    has_scheduler = False
    has_ref_latent = False
    has_guided = False
    has_piflow = False
    has_kontext = False
    has_video = False
    has_inpaint = False
    has_krea = False
    has_ltx = False
    has_ernie = False
    has_ideogram = False
    has_wan = False
    has_qwen = False
    
    for t in types:
        if "TextEncode" in t or "CLIPText" in t:
            has_t2i = True
        if "LoadImage" in t:
            has_i2i = True
        if "MultiLora" in t or "multilora" in t.lower():
            has_multilora = True
        if "Upscale" in t or "upscale" in t.lower():
            has_upscale = True
        if "Scheduler" in t:
            has_scheduler = True
        if "ReferenceLatent" in t:
            has_ref_latent = True
        if "Guided" in t or "Guider" in t:
            has_guided = True
        if "pi-Flow" in t or "PiFlow" in t or "Pi-flux" in t:
            has_piflow = True
        if "Kontext" in t or "ImageStitch" in t:
            has_kontext = True
        if "LTX" in t or "LTX23" in t or "ltx23" in t:
            has_ltx = True
        if "Ernie" in t or "ernie" in t:
            has_ernie = True
        if "Ideogram" in t or "ideogram" in t:
            has_ideogram = True
        if "Wan" in t or "wan" in t:
            has_wan = True
        if "Qwen" in t or "qwen" in t:
            has_qwen = True
        if "Video" in t or "v2v" in t.lower() or "t2v" in t.lower() or "i2v" in t.lower():
            has_video = True
        if "Inpaint" in t or "inpaint" in t:
            has_inpaint = True
        if "Krea" in t:
            has_krea = True
    
    # Determinar categoría principal
    category = "unknown"
    subcategory = ""
    
    if has_video and has_ltx:
        category = "video_ltx"
        subcategory = "ltx_video"
    elif has_video:
        category = "video"
        subcategory = "video_general"
    elif has_piflow:
        category = "t2i"
        subcategory = "flux2_piflow"
    elif has_kontext:
        category = "i2i"
        subcategory = "flux_kontext"
    elif has_multilora and has_i2i:
        category = "i2i"
        subcategory = "i2i_multilora"
    elif has_multilora:
        category = "t2i"
        subcategory = "t2i_multilora"
    elif has_i2i and has_ref_latent and has_guided:
        category = "i2i"
        subcategory = "i2i_refguided"
    elif has_i2i:
        category = "i2i"
        subcategory = "i2i_basic"
    elif has_t2i and has_guided:
        category = "t2i"
        subcategory = "flux2_klein_gguf"
    elif has_t2i:
        category = "t2i"
        subcategory = "t2i_basic"
    
    return {
        "category": category,
        "subcategory": subcategory,
        "has_t2i": has_t2i,
        "has_i2i": has_i2i,
        "has_multilora": has_multilora,
        "has_upscale": has_upscale,
        "has_scheduler": has_scheduler,
        "has_ref_latent": has_ref_latent,
        "has_guided": has_guided,
        "has_piflow": has_piflow,
        "has_kontext": has_kontext,
        "has_video": has_video,
        "has_inpaint": has_inpaint,
        "has_krea": has_krea,
        "has_ltx": has_ltx,
        "has_ernie": has_ernie,
        "has_ideogram": has_ideogram,
        "has_wan": has_wan,
        "has_qwen": has_qwen,
    }


def analyze_all_workflows():
    """Analiza todos los workflows y alimenta la DB."""
    print("Analizando workflows de AcademiaSD/Flux2...")
    print(f"Directorio: {WORLD_DIR}")
    print()
    
    # Obtener todos los archivos JSON
    files = []
    for f in os.listdir(WORLD_DIR):
        if f.endswith(".json"):
            files.append(f)
    
    print(f"Archivos encontrados: {len(files)}")
    print()
    
    # Categorizar todos los workflows
    categories = {}
    
    for fname in sorted(files):
        path = os.path.join(WORLD_DIR, fname)
        
        with open(path) as fh:
            wf = json.load(fh)
        
        # Extraer nodos únicos
        types = set()
        custom_nodes = set()
        academia_nodes = set()
        essential_nodes = set()
        
        for node in wf.get("nodes", []):
            ntype = node.get("type", "")
            props = node.get("properties", {})
            cnr_id = props.get("cnr_id", "")
            
            if ntype:
                types.add(ntype)
            
            if cnr_id:
                custom_nodes.add(ntype)
            
            if "Academia" in ntype or "academia" in ntype.lower():
                academia_nodes.add(ntype)
            
            # Nodos esenciales de cada categoría
            if ntype in ("CLIPTextEncode", "DualCLIPLoader", "VAELoader", "VAEDecode", "VAEEncode", "UNETLoader", "UnetLoaderGGUF", "KSampler", "SaveImage"):
                essential_nodes.add(ntype)
        
        # Clasificar
        classification = classify_workflow(fname, wf)
        cat = classification["category"]
        subcat = classification["subcategory"]
        
        key = f"{cat}_{subcat}" if subcat else cat
        if key not in categories:
            categories[key] = {
                "workflows": [],
                "all_types": set(),
                "academia_nodes": set(),
                "custom_nodes": set(),
                "essential_nodes": set(),
                "flags": {},
            }
        
        categories[key]["workflows"].append(fname)
        categories[key]["all_types"].update(types)
        categories[key]["academia_nodes"].update(academia_nodes)
        categories[key]["custom_nodes"].update(custom_nodes)
        categories[key]["essential_nodes"].update(essential_nodes)
        
        # Acumular flags
        for flag, value in classification.items():
            if isinstance(value, bool):
                if flag not in categories[key]["flags"]:
                    categories[key]["flags"][flag] = False
                categories[key]["flags"][flag] = categories[key]["flags"].get(flag, False) or value
    
    # Resumen por categoría
    print("=" * 80)
    print("RESUMEN POR CATEGORÍA")
    print("=" * 80)
    
    for key in sorted(categories.keys()):
        cat_data = categories[key]
        print(f"\n📂 {key.replace('_', '/')}")
        print(f"   Workflows: {len(cat_data['workflows'])}")
        print(f"   Nodos únicos: {len(cat_data['all_types'])}")
        print(f"   Academia nodes: {len(cat_data['academia_nodes'])}")
        print(f"   Essential nodes: {len(cat_data['essential_nodes'])}")
        
        # Mostrar tipos de nodo
        all_types = sorted(cat_data['all_types'])
        if all_types:
            # Separar en grupos de 5
            for i in range(0, len(all_types), 5):
                chunk = all_types[i:i+5]
                print(f"   Nodos: {' | '.join(chunk)}")
        
        # Mostrar flags relevantes
        flags = cat_data['flags']
        relevant_flags = {
            'has_t2i': 'Texto→Imagen',
            'has_i2i': 'Imagen→Imagen',
            'has_multilora': 'MultiLoRA',
            'has_upscale': 'Upscale',
            'has_scheduler': 'Scheduler',
            'has_ref_latent': 'ReferenceLatent',
            'has_guided': 'Guider/CFG',
            'has_piflow': 'Pi-Flow',
            'has_kontext': 'Kontext',
            'has_video': 'Video',
            'has_inpaint': 'Inpaint',
            'has_krea': 'Krea2',
            'has_ltx': 'LTX-Video',
            'has_qwen': 'Qwen-Image',
        }
        
        active = [v for k, v in relevant_flags.items() if flags.get(k)]
        if active:
            print(f"   Características: {', '.join(active)}")
    
    print("\n" + "=" * 80)
    print("PROXIMOS PASOS:")
    print("1. Cada categoría tiene su conjunto de nodos requeridos")
    print("2. Los nodos AcademiaSD son complementarios (no obligatorios)")
    print("3. Los nodos esenciales (CLIPTextEncode, KSampler, etc.) son universales")
    print("4. Cada workflow puede usarse como template para su categoría")
    print("=" * 80)
    
    return categories


def save_to_db(categories):
    """Guarda la información en la DB."""
    conn = get_conn()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    
    # Borrar datos anteriores de este análisis
    c.execute("DELETE FROM workflow_analysis WHERE workflow_id LIKE 'academia_%'")
    c.execute("DELETE FROM node_usage_stats WHERE class_type LIKE 'academia_%'")
    
    # Guardar cada categoría como un "workflow virtual"
    for key, cat_data in categories.items():
        wf_id = f"academia_{key.replace('/', '_')}"
        
        # Guardar la categoría como registro de análisis
        all_types = sorted(cat_data['all_types'])
        academia = sorted(cat_data['academia_nodes'])
        essential = sorted(cat_data['essential_nodes'])
        
        c.execute("""
            INSERT INTO workflow_analysis 
            (workflow_id, timestamp, status, missing_nodes, extra_nodes, resolved_deps)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            wf_id,
            now,
            "ok",
            json.dumps(academia),      # missing_nodes = nodos AcademiaSD necesarios
            json.dumps(essential),     # extra_nodes = nodos esenciales
            json.dumps(all_types),     # resolved_deps = todos los tipos de nodo
        ))
        
        # Registrar cada tipo de nodo como usado
        for ntype in all_types:
            c.execute("""
                INSERT INTO node_usage_stats (class_type, last_used, usage_count)
                VALUES (?, ?, 1)
                ON CONFLICT(class_type) DO UPDATE SET
                    last_used = excluded.last_used,
                    usage_count = usage_count + 1
            """, (f"academia:{ntype}", now, 1))
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ Guardados {len(categories)} categorías en la DB")
    print(f"   Cada categoría = 1 registro en workflow_analysis")
    print(f"   Cada tipo de nodo = 1 registro en node_usage_stats")


if __name__ == "__main__":
    categories = analyze_all_workflows()
    save_to_db(categories)
    
    # Resumen final
    print("\n" + "=" * 80)
    print("RESUMEN FINAL - WORKFLOWS ACADEMIA SD / FLUX2")
    print("=" * 80)
    
    total = 0
    for key, cat_data in categories.items():
        wfs = cat_data['workflows']
        total += len(wfs)
        print(f"  {key.replace('_', '/'):50s} → {len(wfs)} workflows, {len(cat_data['all_types'])} nodos")
    
    print(f"\n  TOTAL: {total} workflows en {len(categories)} categorías")
