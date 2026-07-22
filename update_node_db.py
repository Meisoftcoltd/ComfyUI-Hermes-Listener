"""
update_node_db.py - Actualiza la base de datos del mecánico con datos frescos de /object_info.

Uso:
  python3 update_node_db.py           # Update completo (lento pero completo)
  python3 update_node_db.py --fast    # Solo actualizar nodos y conexiones (rápido)
  python3 update_node_db.py --status  # Ver estado de la DB

Se puede programar como cron job para mantener la DB sincronizada.
"""

import json
import os
import sys
import urllib.request
import sqlite3
from collections import defaultdict, Counter
from datetime import datetime


def load_catalog():
    """Cargar catálogo de nodos custom."""
    catalog_path = "/home/meisoft/ComfyUI-Hermes-Listener/all_custom_nodes_catalog.json"
    if not os.path.exists(catalog_path):
        return []
    with open(catalog_path) as f:
        catalog = json.load(f)
    catalog_map = {}
    for entry in catalog:
        catalog_map[entry['node']] = {
            'package': entry['package'],
            'inputs': entry['inputs'],
            'outputs': entry['outputs']
        }
    return catalog_map


def get_object_info():
    """Obtener /object_info de ComfyUI."""
    url = "http://127.0.0.1:8189/object_info"
    req = urllib.request.Request(url)
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())


def update_nodes(db_c, obj_info, catalog_map):
    """Actualizar tabla nodes desde /object_info y catálogo."""
    custom_types = set(catalog_map.keys())
    updated = 0

    for ct, info in obj_info.items():
        if 'input' not in info:
            continue

        inputs_raw = info.get("input", {})
        outputs_raw = info.get("output", [])

        # Normalizar inputs
        input_list = []
        for section in ("required", "optional"):
            for name, spec in inputs_raw.get(section, {}).items():
                input_list.append({
                    "name": name,
                    "type": spec[0] if isinstance(spec, (list, tuple)) else str(spec),
                    "required": section == "required",
                    "default": spec[1] if isinstance(spec, (list, tuple)) and len(spec) > 1 else None
                })

        # Normalizar outputs
        output_list = []
        if isinstance(outputs_raw, list):
            for out in outputs_raw:
                if isinstance(out, str):
                    output_list.append({"name": out, "type": out})
                elif isinstance(out, (list, tuple)):
                    output_list.append({"name": out[0] if out else "", "type": out[1] if len(out) > 1 else ""})
                elif isinstance(out, dict):
                    output_list.append({
                        "name": out.get("name", ""),
                        "type": out.get("type", "")
                    })

        name = info.get("name", ct)
        desc = info.get("description", "")
        python_module = info.get("python_module", "")

        # Determinar si es custom
        is_custom = ct in custom_types
        if is_custom and python_module:
            # Extraer nombre de paquete del path del módulo
            parts = python_module.split('.')
            pkg_name = parts[0] if parts else ct
        elif is_custom:
            # Buscar en catálogo
            if ct in catalog_map:
                pkg_name = f"custom_nodes.{catalog_map[ct]['package']}"
            else:
                pkg_name = "custom_nodes.unknown"
        else:
            pkg_name = "comfy.nodes"
            if not python_module:
                python_module = "comfy.nodes"

        # Generar descripción si falta
        if not desc or len(desc) < 5:
            parts_desc = [f"Nodo de {pkg_name.split('.')[-1]}"]
            if input_list:
                names = [i.get('name', i.get('type', '?')) for i in input_list[:3]]
                parts_desc.append("Entradas: " + ", ".join(names))
            if output_list:
                names = [o.get('name', o.get('type', '?')) for o in output_list[:3]]
                parts_desc.append("Salidas: " + ", ".join(names))
            desc = " | ".join(parts_desc)

        now = datetime.utcnow().isoformat()
        db_c.execute("""
            INSERT OR REPLACE INTO nodes
            (class_type, name, description, builtin, python_module, input_params, output_params, last_scanned)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ct, name, desc, 1 if not is_custom else 0,
            pkg_name, json.dumps(input_list), json.dumps(output_list), now
        ))
        updated += 1

    db_conn.commit()
    return updated


def update_connections(db_c, obj_info):
    """Actualizar data_type de conexiones desde /object_info."""
    updated = 0

    db_c.execute("SELECT source_class_type, source_param, target_class_type, target_param, data_type FROM node_connections")
    conns = list(db_c.fetchall())

    for conn in conns:
        source = conn['source_class_type']
        if source not in obj_info:
            continue

        outputs = obj_info[source].get('output', [])
        if not isinstance(outputs, list) or len(outputs) < 2:
            continue

        actual_type = outputs[1] if isinstance(outputs[1], str) else str(outputs[1])

        if conn['data_type'] and conn['data_type'] != actual_type:
            db_c.execute("""
                UPDATE node_connections SET data_type = ?
                WHERE source_class_type = ? AND source_param = ?
                AND target_class_type = ? AND target_param = ?
            """, (actual_type, conn['source_class_type'], conn['source_param'],
                  conn['target_class_type'], conn['target_param']))
            updated += 1

    db_conn.commit()
    return updated


def compute_upstream_downstream(db_c):
    """Computar upstream/downstream connections para cada nodo."""
    db_c.execute("SELECT class_type, input_params, output_params FROM nodes")
    all_nodes = {}
    for row in db_c.fetchall():
        ct = row['class_type']
        try:
            inputs = json.loads(row['input_params']) if row['input_params'] else []
        except:
            inputs = []
        try:
            outputs = json.loads(row['output_params']) if row['output_params'] else []
        except:
            outputs = []
        all_nodes[ct] = {'inputs': inputs, 'outputs': outputs}

    # Índice inverso: output_type -> list of producers
    output_index = defaultdict(list)
    for ct, info in all_nodes.items():
        for out in info['outputs']:
            if isinstance(out, dict) and out.get('type'):
                otype = out['type']
                if isinstance(otype, list):
                    for item in otype:
                        output_index[item].append(ct)
                else:
                    output_index[otype].append(ct)

    # Índice directo: input_type -> list of consumers
    input_index = defaultdict(list)
    for ct, info in all_nodes.items():
        for inp in info['inputs']:
            if isinstance(inp, dict) and 'type' in inp:
                t = inp['type']
                if isinstance(t, list):
                    for item in t:
                        input_index[item].append(ct)
                else:
                    input_index[t].append(ct)

    # Computar upstream (quién puede alimentar cada nodo)
    upstream_count = 0
    for ct, info in all_nodes.items():
        upstreams = []
        for inp in info['inputs']:
            if isinstance(inp, dict) and 'type' in inp:
                t = inp['type']
                if isinstance(t, list):
                    for item in t:
                        for prod in output_index.get(item, []):
                            if prod not in upstreams:
                                upstreams.append(prod)
                else:
                    for prod in output_index.get(t, []):
                        if prod not in upstreams:
                            upstreams.append(prod)

        if upstreams:
            db_c.execute("UPDATE nodes SET upstream_nodes = ? WHERE class_type = ?",
                         (json.dumps(upstreams[:20]), ct))
            upstream_count += 1

    # Computar downstream (a quién alimenta cada nodo)
    downstream_count = 0
    for ct, info in all_nodes.items():
        downstreams = []
        for out in info['outputs']:
            if isinstance(out, dict) and out.get('type'):
                otype = out['type']
                if isinstance(otype, list):
                    for item in otype:
                        for consumer in input_index.get(item, []):
                            if consumer not in downstreams:
                                downstreams.append(consumer)
                else:
                    for consumer in input_index.get(otype, []):
                        if consumer not in downstreams:
                            downstreams.append(consumer)

        if downstreams:
            db_c.execute("UPDATE nodes SET downstream_nodes = ? WHERE class_type = ?",
                         (json.dumps(downstreams[:10]), ct))
            downstream_count += 1

    return upstream_count, downstream_count


def compute_common_connections(db_c):
    """Computar conexiones comunes entre nodos."""
    db_c.execute("SELECT source_class_type, target_class_type FROM node_connections")
    conns = list(db_c.fetchall())

    node_common = defaultdict(lambda: Counter())
    for conn in conns:
        s, t = conn
        node_common[s][t] += 1
        node_common[t][s] += 1

    # Get all nodes that need updating
    db_c.execute("SELECT class_type FROM nodes WHERE common_connections IS NULL OR common_connections = '[]' OR common_connections = ''")
    all_node_types = [row[0] for row in db_c.fetchall()]

    count = 0
    for ct in all_node_types:
        if ct in node_common:
            top3 = [k for k, v in node_common[ct].most_common(3)]
        else:
            top3 = []
        db_c.execute("UPDATE nodes SET common_connections = ? WHERE class_type = ?",
                     (json.dumps(top3), ct))
        count += 1

    db_conn.commit()
    return count


def compute_fallbacks(db_c, catalog_map):
    """Generar fallbacks para nodos faltantes comunes."""
    known_custom = set(catalog_map.keys())

    # Mapeo de nodos faltantes -> fallbacks conocidos
    fallback_map = {
        "EmptyFluxLatentImage": "EmptyLatentImage",
        "VAEEncodeForInpaintWithPadding": "VAEEncodeForInpaint",
        "CheckpointLoaderSimpleWithNoiseCombiner": "CheckpointLoaderSimple",
        "CLIPTextEncodeFlux": "CLIPTextEncode",
        "EmptySD3LatentImage": "EmptyLatentImage",
    }

    count = 0
    for missing, fallback in fallback_map.items():
        if missing in known_custom:
            db_c.execute("""
                INSERT OR REPLACE INTO node_fallbacks
                (class_type, fallback_class_type, reason, created_at)
                VALUES (?, ?, ?, ?)
            """, (missing, fallback, "Nodo faltante no instalado pero común en workflows",
                  datetime.utcnow().isoformat()))
            count += 1

    return count


def scan_templates(db_c):
    """Escane templates JSON en templates/."""
    templates_dir = "/home/meisoft/ComfyUI-Hermes-Listener/templates"
    if not os.path.exists(templates_dir):
        return 0

    now = datetime.utcnow().isoformat()
    count = 0

    for tf in os.listdir(templates_dir):
        if not tf.endswith('.json'):
            continue
        tfpath = os.path.join(templates_dir, tf)
        try:
            with open(tfpath) as f:
                wf = json.load(f)
            node_types = set()
            for nid, ndata in wf.items():
                if isinstance(ndata, dict):
                    ct = ndata.get('class_type', '')
                    if ct:
                        node_types.add(ct)
            db_c.execute(
                "INSERT OR REPLACE INTO templates (id, name, file_path, node_count, nodes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (1, tf, tfpath, len(node_types), json.dumps(sorted(node_types)), now))
            count += 1
        except:
            pass

    return count


def show_status():
    """Mostrar estado de la DB."""
    db_c.execute("SELECT COUNT(*) FROM nodes WHERE builtin = 0")
    custom = db_c.fetchone()[0]
    db_c.execute("SELECT COUNT(*) FROM nodes WHERE builtin = 1")
    native = db_c.fetchone()[0]
    db_c.execute("SELECT COUNT(*) FROM nodes WHERE description != '' AND description IS NOT NULL")
    with_desc = db_c.fetchone()[0]
    db_c.execute("SELECT COUNT(*) FROM nodes WHERE upstream_nodes IS NOT NULL AND upstream_nodes NOT IN ('[]', '')")
    with_upstream = db_c.fetchone()[0]
    db_c.execute("SELECT COUNT(*) FROM nodes WHERE downstream_nodes IS NOT NULL AND downstream_nodes NOT IN ('[]', '')")
    with_downstream = db_c.fetchone()[0]
    db_c.execute("SELECT COUNT(*) FROM nodes WHERE common_connections IS NOT NULL AND common_connections NOT IN ('[]', '')")
    with_conns = db_c.fetchone()[0]
    db_c.execute("SELECT COUNT(*) FROM node_connections WHERE data_type != '' AND data_type IS NOT NULL")
    typed = db_c.fetchone()[0]
    db_c.execute("SELECT COUNT(*) FROM node_fallbacks")
    fallbacks = db_c.fetchone()[0]
    db_c.execute("SELECT COUNT(*) FROM templates")
    templates = db_c.fetchone()[0]

    total = custom + native
    print(f"\n{'='*60}")
    print(f"📊 ESTADO DE LA BASE DE DATOS")
    print(f"{'='*60}")
    print(f"  Custom nodes (builtin=0):  {custom}")
    print(f"  Native nodes (builtin=1):  {native}")
    print(f"  Total nodos:               {total}")
    print(f"  Con descripción completa:   {with_desc}/{total}")
    print(f"  Con info upstream:          {with_upstream}")
    print(f"  Con info downstream:        {with_downstream}")
    print(f"  Con common_connections:     {with_conns}")
    print(f"  Conexiones con data_type:   {typed}")
    print(f"  Fallbacks:                  {fallbacks}")
    print(f"  Templates escaneados:       {templates}")
    print(f"{'='*60}")


# ─── MAIN ───
if __name__ == "__main__":
    db_path = "/home/meisoft/ComfyUI-Hermes-Listener/comfyui_nodes.db"

    if "--status" in sys.argv:
        db_conn = sqlite3.connect(db_path)
        db_conn.row_factory = sqlite3.Row
        db_c = db_conn.cursor()
        show_status()
        db_conn.close()
        sys.exit(0)

    # Initialize DB tables
    db_conn = sqlite3.connect(db_path)
    db_conn.row_factory = sqlite3.Row
    db_c = db_conn.cursor()

    # Create tables
    db_c.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            class_type TEXT PRIMARY KEY,
            name TEXT,
            description TEXT,
            builtin INTEGER DEFAULT 1,
            python_module TEXT,
            input_params TEXT,
            output_params TEXT,
            last_scanned TEXT,
            upstream_nodes TEXT,
            downstream_nodes TEXT,
            common_connections TEXT
        )
    """)
    db_c.execute("""
        CREATE TABLE IF NOT EXISTS node_connections (
            id INTEGER PRIMARY KEY,
            source_class_type TEXT NOT NULL,
            source_param TEXT NOT NULL,
            target_class_type TEXT NOT NULL,
            target_param TEXT NOT NULL,
            data_type TEXT,
            FOREIGN KEY(source_class_type) REFERENCES nodes(class_type),
            FOREIGN KEY(target_class_type) REFERENCES nodes(class_type)
        )
    """)
    db_c.execute("""
        CREATE TABLE IF NOT EXISTS node_fallbacks (
            class_type TEXT PRIMARY KEY,
            fallback_class_type TEXT NOT NULL,
            reason TEXT,
            created_at TEXT
        )
    """)
    db_c.execute("""
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            node_count INTEGER,
            nodes TEXT,
            created_at TEXT
        )
    """)
    db_conn.commit()

    catalog_map = load_catalog()
    obj_info = get_object_info()

    print("Updating nodes...")
    update_nodes(db_c, obj_info, catalog_map)
    print("Updating connections...")
    update_connections(db_c, obj_info)
    print("Computing upstream/downstream...")
    up, down = compute_upstream_downstream(db_c)
    print("Computing common connections...")
    cc = compute_common_connections(db_c)
    print("Computing fallbacks...")
    fb = compute_fallbacks(db_c, catalog_map)
    print("Scanning templates...")
    tmpl = scan_templates(db_c)

    show_status()
    print(f"\n✅ Actualización completa. {len(obj_info)} nodos procesados.")
    db_conn.close()
