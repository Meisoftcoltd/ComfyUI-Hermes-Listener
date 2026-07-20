#!/usr/bin/env python3
"""
import_academia_workflows.py — Importa workflows de AcademiaSD/Flux2 en la DB
de comfyui_nodes como plantillas de referencia.

Cada categoría de workflow se guarda como un registro en workflow_analysis
con:
  - workflow_id: academia_<categoria>
  - missing_nodes: nodos AcademiaSD necesarios (complementarios)
  - extra_nodes: nodos esenciales universales
  - resolved_deps: todos los tipos de nodo usados (plantilla)

Cada nodo se registra en node_usage_stats con count=1 (plantilla).

También se crean conexiones entre nodos clave por cada categoría.
"""

import sqlite3
import json
import os
import sys
from datetime import datetime
from datetime import timezone

DB_PATH = "/home/meisoft/ComfyUI-Hermes-Listener/comfyui_nodes.db"
WORLD_DIR = "/home/meisoft/comfyui_AcademiaSD/example_workflows"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def extract_node_types(wf):
    """Extrae tipos de nodo, nodos AcademiaSD y esenciales."""
    types = set()
    academia_nodes = set()
    essential_nodes = set()
    all_node_info = {}

    ESSENTIAL_MAP = {
        't2i_basic': [
            'CLIPLoader', 'CLIPTextEncode', 'EmptyLatentImage',
            'UNETLoader', 'KSampler', 'VAEDecode', 'SaveImage'
        ],
        't2i_multilora': [
            'CLIPLoader', 'CLIPTextEncode', 'EmptyLatentImage',
            'UNETLoader', 'KSampler', 'VAEDecode', 'SaveImage'
        ],
        'i2i_basic': [
            'CLIPTextEncode', 'DualCLIPLoader', 'LoadImage',
            'KSampler', 'VAEDecode', 'VAEEncode', 'VAELoader', 'SaveImage'
        ],
        'i2i_multilora': [
            'CLIPTextEncode', 'LoadImage', 'KSampler',
            'VAEDecode', 'VAEEncode', 'VAELoader', 'SaveImage'
        ],
        'i2i_refguided': [
            'CLIPTextEncode', 'LoadImage', 'ReferenceLatent',
            'KSampler', 'VAEDecode', 'VAEEncode', 'VAELoader', 'SaveImage'
        ],
        'flux2_klein_gguf': [
            'CLIPTextEncode', 'ClipLoaderGGUF', 'UnetLoaderGGUF',
            'FluxGuidance', 'Flux2Scheduler', 'EmptyFlux2LatentImage',
            'KSampler', 'VAEDecode', 'VAEEncode', 'VAELoader', 'SaveImage'
        ],
        'flux2_piflow': [
            'CLIPTextEncode', 'ClipLoaderGGUF', 'UnetLoaderGGUF',
            'FluxGuidance', 'EmptyFlux2LatentImage',
            'VAEDecode', 'VAEEncode', 'VAELoader', 'SaveImage'
        ],
        'flux_kontext': [
            'CLIPTextEncode', 'DualCLIPLoader', 'LoadImage',
            'ReferenceLatent', 'KSampler', 'VAEDecode',
            'VAEEncode', 'VAELoader', 'SaveImage'
        ],
        'video_ltx': [
            'CLIPTextEncode', 'DualCLIPLoader',
            'KSampler', 'VAEDecode', 'VAELoader', 'SaveImage'
        ],
        'video_general': [
            'CLIPTextEncode', 'DualCLIPLoader',
            'KSampler', 'VAEDecode', 'VAELoader', 'SaveImage'
        ],
    }

    for node in wf.get("nodes", []):
        ntype = node.get("type", "")
        props = node.get("properties", {})
        cnr_id = props.get("cnr_id", "")

        if ntype:
            types.add(ntype)

            all_node_info[ntype] = {
                'cnr_id': cnr_id,
                'is_custom': bool(cnr_id),
                'is_academia': "Academia" in ntype or "academia" in ntype.lower(),
                'inputs': [],
                'outputs': [],
            }

        for cat, ess in ESSENTIAL_MAP.items():
            if ntype in ess:
                essential_nodes.add(ntype)
                break

        if "Academia" in ntype or "academia" in ntype.lower():
            academia_nodes.add(ntype)

    return types, academia_nodes, essential_nodes, all_node_info


def extract_connections(wf):
    """
    Extrae conexiones entre nodos de un workflow.
    Los links son IDs numéricos que conectan outputs de un nodo con inputs de otro.
    """
    connections = []
    all_nodes = wf.get("nodes", [])

    # Crear mapa: link_id -> (source_nodetype, source_output_name)
    link_to_source = {}
    for node in all_nodes:
        nid = node.get("id", "")
        ntype = node.get("type", "")
        outputs = node.get("outputs", [])

        for out in (outputs or []):
            if isinstance(out, dict):
                out_name = out.get("name", "")
                out_links = out.get("links", [])
                if isinstance(out_links, list):
                    for link_id in out_links:
                        if isinstance(link_id, int):
                            link_to_source[link_id] = (ntype, out_name)

    # Ahora para cada input que tiene link, encontrar el origen
    for node in all_nodes:
        ntype = node.get("type", "")
        inputs = node.get("inputs", [])

        for inp in (inputs or []):
            if isinstance(inp, dict):
                link_id = inp.get("link")
                if isinstance(link_id, int) and link_id in link_to_source:
                    src_type, src_param = link_to_source[link_id]
                    param_type = inp.get("type", "*")
                    target_param = inp.get("name", "")

                    # Usar str() para evitar problemas con % en los nombres
                    src_key = "academia:" + str(src_type)
                    tgt_key = "academia:" + str(ntype)
                    src_p = str(src_param)
                    tgt_p = str(target_param)
                    p_type = str(param_type)

                    connections.append((src_key, src_p, tgt_key, tgt_p, p_type))

    return connections


def classify_workflow(fname, wf):
    """Clasifica un workflow según sus nodos."""
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

    category = "unknown"
    subcategory = ""

    if has_video and has_ltx:
        category = "video"
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


def main():
    print("=" * 80)
    print("IMPORTACIÓN DE WORKFLOWS ACADEMIA SD / FLUX2 EN LA DB")
    print("=" * 80)

    # Obtener todos los archivos JSON
    files = []
    for f in os.listdir(WORLD_DIR):
        if f.endswith(".json"):
            files.append(f)

    print(f"Archivos encontrados: {len(files)}")

    # Categorizar todos los workflows
    categories = {}
    total_connections = 0

    for fname in sorted(files):
        path = os.path.join(WORLD_DIR, fname)

        with open(path) as fh:
            wf = json.load(fh)

        # Extraer info
        types, academia_nodes, essential_nodes, node_info = extract_node_types(wf)
        connections = extract_connections(wf)
        classification = classify_workflow(fname, wf)

        cat = classification["category"]
        subcat = classification["subcategory"]
        key = f"{cat}_{subcat}" if subcat else cat

        if key not in categories:
            categories[key] = {
                "workflows": [],
                "all_types": set(),
                "academia_nodes": set(),
                "essential_nodes": set(),
                "all_connections": set(),
                "flags": {},
                "node_details": {},
            }

        categories[key]["workflows"].append(fname)
        categories[key]["all_types"].update(types)
        categories[key]["academia_nodes"].update(academia_nodes)
        categories[key]["essential_nodes"].update(essential_nodes)

        # Acumular conexiones únicas (usar tupla como clave)
        for conn in connections:
            # Normalizar cada elemento como str para evitar problemas con %
            normed = tuple(str(c) for c in conn)
            categories[key]["all_connections"].add(normed)
            total_connections += 1

        # Acumular detalles de nodos
        for ntype, info in node_info.items():
            if ntype not in categories[key]["node_details"]:
                categories[key]["node_details"][ntype] = info

        # Acumular flags
        for flag, value in classification.items():
            if isinstance(value, bool):
                if flag not in categories[key]["flags"]:
                    categories[key]["flags"][flag] = False
                categories[key]["flags"][flag] = categories[key]["flags"].get(flag, False) or value

    # Resumen por categoría
    print(f"\nCategorías encontradas: {len(categories)}\n")
    print("-" * 80)

    for key in sorted(categories.keys()):
        cat_data = categories[key]
        flags = cat_data['flags']

        cat_name = key.replace('_', '/')
        wfs = cat_data['workflows']
        all_types = cat_data['all_types']
        academia = cat_data['academia_nodes']
        essential = cat_data['essential_nodes']

        print(f"\n📂 {cat_name}")
        print(f"   Workflows: {len(wfs)}")
        print(f"   Nodos únicos: {len(all_types)}")
        print(f"   Academia nodes: {len(academia)}")
        print(f"   Nodos esenciales: {len(essential)}")
        print(f"   Conexiones únicas: {len(cat_data['all_connections'])}")

        # Mostrar características
        char_map = {
            'has_t2i': 'T2I', 'has_i2i': 'I2I', 'has_multilora': 'MultiLoRA',
            'has_upscale': 'Upscale', 'has_scheduler': 'Scheduler',
            'has_ref_latent': 'RefLatent', 'has_guided': 'Guider',
            'has_piflow': 'PiFlow', 'has_kontext': 'Kontext',
            'has_video': 'Video', 'has_inpaint': 'Inpaint',
            'has_krea': 'Krea2', 'has_ltx': 'LTX-Video',
            'has_qwen': 'Qwen',
        }

        active = [v for k, v in char_map.items() if flags.get(k)]
        if active:
            print(f"   Features: {', '.join(active)}")

    # Guardar en DB
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    # Desactivar foreign keys para evitar errores de orden de inserción
    conn.execute("PRAGMA foreign_keys = OFF")
    
    # Borrar datos anteriores de AcademiaSD
    c.execute("DELETE FROM workflow_analysis WHERE workflow_id LIKE 'academia_%'")
    c.execute("DELETE FROM node_usage_stats WHERE class_type LIKE 'academia:%'")
    c.execute("DELETE FROM node_connections WHERE source_class_type LIKE 'academia:%' OR target_class_type LIKE 'academia:%'")
    conn.commit()

    total_wfs = 0
    total_nodes = 0

    for key, cat_data in categories.items():
        wf_id = f"academia_{key.replace('/', '_')}"

        all_types = sorted(cat_data['all_types'])
        academia = sorted(cat_data['academia_nodes'])
        essential = sorted(cat_data['essential_nodes'])
        connections = sorted(cat_data['all_connections'])

        # Guardar categoría como registro de análisis
        c.execute("""
            INSERT INTO workflow_analysis
            (workflow_id, timestamp, status, missing_nodes, extra_nodes, resolved_deps)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            wf_id,
            now,
            "ok",
            json.dumps(academia),
            json.dumps(essential),
            json.dumps(all_types),
        ))

        # Registrar cada tipo de nodo como usado (para templates)
        for ntype in all_types:
            c.execute("""
                INSERT INTO node_usage_stats (class_type, last_used, usage_count)
                VALUES (?, ?, 1)
                ON CONFLICT(class_type) DO UPDATE SET
                    last_used = excluded.last_used,
                    usage_count = usage_count + 1
            """, (f"academia:{ntype}", now))
            total_nodes += 1

        # Guardar conexiones
        for src, src_param, tgt, tgt_param, ptype in connections:
            c.execute("""
                INSERT OR IGNORE INTO node_connections
                (source_class_type, source_param, target_class_type, target_param, data_type, is_valid, registered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (src, src_param, tgt, tgt_param, ptype, 1, now))

        total_wfs += len(cat_data["workflows"])

    conn.commit()
    conn.close()

    print("\n" + "=" * 80)
    print("RESUMEN FINAL")
    print("=" * 80)
    print(f"  ✓ {len(categories)} categorías importadas")
    print(f"  ✓ {total_wfs} workflows analizados")
    print(f"  ✓ {total_nodes} registros de nodos en node_usage_stats")
    print(f"  ✓ {total_connections} conexiones registradas en node_connections")
    print(f"  ✓ Cada categoría tiene su plantilla de nodos en workflow_analysis")
    print("=" * 80)
    print("\nPRÓXIMOS PASOS:")
    print("  1. El agente puede consultar get_compatible_nodes() para encontrar nodos")
    print("  2. Cada categoría tiene sus nodos esenciales y complementarios")
    print("  3. Las conexiones registradas sirven como patrón para ensamblar workflows")
    print("  4. El agente puede elegir plantilla según el prompt del usuario")


if __name__ == "__main__":
    main()
