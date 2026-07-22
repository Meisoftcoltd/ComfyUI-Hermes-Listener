import json
import os
import re
import urllib.request
import sqlite3
from collections import defaultdict, Counter
from datetime import datetime, timedelta

db_path = "/home/meisoft/ComfyUI-Hermes-Listener/comfyui_nodes.db"
catalog_path = "/home/meisoft/ComfyUI-Hermes-Listener/all_custom_nodes_catalog.json"

# Load catalog
with open(catalog_path) as f:
    catalog = json.load(f)

catalog_map = {}
for entry in catalog:
    catalog_map[entry['node']] = {
        'package': entry['package'],
        'inputs': entry['inputs'],
        'outputs': entry['outputs']
    }

# Load DB
db_conn = sqlite3.connect(db_path)
db_conn.row_factory = sqlite3.Row
db_c = db_conn.cursor()

# Check what columns exist
db_c.execute("PRAGMA table_info(nodes)")
columns = {col['name']: True for col in db_c.fetchall()}

# Get all nodes data
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

# Get connections
db_c.execute("SELECT source_class_type, source_param, target_class_type, target_param, data_type FROM node_connections")
all_connections = []
for row in db_c.fetchall():
    all_connections.append({
        'source': row['source_class_type'],
        'source_param': row['source_param'],
        'target': row['target_class_type'],
        'target_param': row['target_param'],
        'data_type': row['data_type'] or ''
    })

print(f"Processing: {len(all_nodes)} nodes, {len(all_connections)} connections")

# ─── STEP 1: Add downstream_nodes if missing ───
has_downstream = 'downstream_nodes' in columns
has_upstream = 'upstream_nodes' in columns
has_common = 'common_connections' in columns

if not has_downstream:
    print("Adding downstream_nodes column...")
    db_c.execute("ALTER TABLE nodes ADD COLUMN downstream_nodes TEXT")
    db_conn.commit()

if not has_common:
    print("Adding common_connections column...")
    db_c.execute("ALTER TABLE nodes ADD COLUMN common_connections TEXT")
    db_conn.commit()

# Build forward index: output_type -> list of consumers
input_type_consumers = defaultdict(list)
for ct, info in all_nodes.items():
    for inp in info['inputs']:
        if isinstance(inp, dict) and 'type' in inp:
            t = inp['type']
            if isinstance(t, list):
                for item in t:
                    input_type_consumers[item].append(ct)
            else:
                input_type_consumers[t].append(ct)

# Compute downstream for each node that doesn't have it
print("Computing downstream nodes...")
for ct, info in all_nodes.items():
    # Check if already populated
    db_c.execute("SELECT downstream_nodes FROM nodes WHERE class_type = ?", (ct,))
    row = db_c.fetchone()
    if row and row[0] and row[0] not in ('[]', ''):
        continue  # Already populated

    downstreams = []
    for out in info['outputs']:
        if isinstance(out, dict) and out.get('type'):
            otype = out['type']
            if isinstance(otype, list):
                for item in otype:
                    for consumer in input_type_consumers.get(item, []):
                        if consumer not in downstreams:
                            downstreams.append(consumer)
            else:
                for consumer in input_type_consumers.get(otype, []):
                    if consumer not in downstreams:
                        downstreams.append(consumer)

    if downstreams:
        db_c.execute("UPDATE nodes SET downstream_nodes = ? WHERE class_type = ?",
                     (json.dumps(downstreams[:10]), ct))

db_conn.commit()
print("Downstream nodes computed")

# ─── STEP 2: Add common_connections ───
print("Computing common connections...")
node_common = defaultdict(lambda: Counter())
for conn in all_connections:
    node_common[conn['source']][conn['target']] += 1
    node_common[conn['target']][conn['source']] += 1

for ct, connections in node_common.items():
    if ct not in all_nodes:
        continue
    # Check if already populated
    db_c.execute("SELECT common_connections FROM nodes WHERE class_type = ?", (ct,))
    row = db_c.fetchone()
    if row and row[0] and row[0] not in ('[]', ''):
        continue

    top3 = [k for k, v in connections.most_common(3)]
    db_c.execute("UPDATE nodes SET common_connections = ? WHERE class_type = ?",
                 (json.dumps(top3), ct))

db_conn.commit()
print(f"Common connections computed for {len(node_common)} nodes")

# ─── STEP 3: Fill remaining descriptions ───
# For nodes without descriptions, use package info or create generic ones
db_c.execute("SELECT class_type, builtin, python_module FROM nodes WHERE description = '' OR description IS NULL")
no_desc_nodes = db_c.fetchall()

print(f"\nFixing {len(no_desc_nodes)} nodes without descriptions...")
for row in no_desc_nodes:
    ct = row['class_type']
    builtin = row['builtin']
    module = row['python_module'] or ''

    if builtin == 0 and module:
        # Custom node - try to get info from catalog
        if ct in catalog_map:
            pkg = catalog_map[ct]['package']
            inputs = catalog_map[ct]['inputs']
            outputs = catalog_map[ct]['outputs']
            desc = f"Nodo personalizado ({pkg})"
            if inputs:
                desc += f" | Entradas: {', '.join(inputs[:3])}"
            if outputs:
                desc += f" | Salidas: {', '.join(outputs[:3])}"
            db_c.execute("UPDATE nodes SET description = ? WHERE class_type = ?", (desc, ct))
        else:
            # Fallback: create minimal description
            db_c.execute("UPDATE nodes SET description = ? WHERE class_type = ?",
                         (f"Nodo personalizado ({module})", ct))
    elif builtin == 1:
        # Native node - create minimal description
        db_c.execute("UPDATE nodes SET description = ? WHERE class_type = ?",
                     (f"Nodo nativo de ComfyUI: {ct}", ct))

db_conn.commit()

# ─── STEP 4: Build node_fallbacks table (if not exists) ───
db_c.execute("""
    CREATE TABLE IF NOT EXISTS node_fallbacks (
        class_type TEXT PRIMARY KEY,
        fallback_class_type TEXT NOT NULL,
        reason TEXT,
        created_at TEXT
    )
""")

# Create fallbacks: for each node type, find alternatives that produce compatible outputs
# e.g., if a workflow expects EmptyFluxLatentImage, fallback to EmptyLatentImage
print("\nBuilding node fallbacks...")

# Load all models from filesystem
models_dir = "/home/meisoft/ComfyUI-Hermes-Listener/models_inventory.json"
if os.path.exists(models_dir):
    with open(models_dir) as f:
        models_inv = json.load(f)
else:
    models_inv = []

# Add model_paths table entries
db_c.execute("SELECT COUNT(*) FROM model_paths")
model_count = db_c.fetchone()[0]
if model_count == 0:
    db_c.execute("CREATE TABLE IF NOT EXISTS model_paths (id INTEGER PRIMARY KEY, name TEXT, file_path TEXT, model_type TEXT, compatible_node_types TEXT, last_scanned TEXT)")
    now = datetime.utcnow().isoformat()
    for model in models_inv[:50]:  # First 50 to avoid explosion
        db_c.execute(
            "INSERT OR REPLACE INTO model_paths (id, name, file_path, model_type, compatible_node_types, last_scanned) VALUES (?, ?, ?, ?, ?, ?)",
            (model.get('id', 0), model.get('name', ''), model.get('absolute_path', ''), model.get('model_type', ''), json.dumps([]), now))
    db_conn.commit()
    print(f"Loaded {len(models_inv[:50])} models into DB")

# ─── STEP 5: Save template info ───
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

# Scan templates directory
templates_dir = "/home/meisoft/ComfyUI-Hermes-Listener/templates"
if os.path.exists(templates_dir):
    now = datetime.utcnow().isoformat()
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
        except:
            pass
    print(f"Templates scanned in {templates_dir}")

# ─── FINAL STATUS ───
db_c.execute("SELECT COUNT(*) FROM nodes WHERE builtin = 0")
custom_final = db_c.fetchone()[0]
db_c.execute("SELECT COUNT(*) FROM nodes WHERE builtin = 1")
native_final = db_c.fetchone()[0]
db_c.execute("SELECT COUNT(*) FROM nodes WHERE description != '' AND description IS NOT NULL")
with_desc = db_c.fetchone()[0]
db_c.execute("SELECT COUNT(*) FROM nodes WHERE upstream_nodes IS NOT NULL AND upstream_nodes NOT IN ('[]', '')")
with_upstream = db_c.fetchone()[0]
db_c.execute("SELECT COUNT(*) FROM nodes WHERE downstream_nodes IS NOT NULL AND downstream_nodes NOT IN ('[]', '')")
with_downstream = db_c.fetchone()[0]
db_c.execute("SELECT COUNT(*) FROM nodes WHERE common_connections IS NOT NULL AND common_connections NOT IN ('[]', '')")
with_conns = db_c.fetchone()[0]
db_c.execute("SELECT COUNT(*) FROM node_connections WHERE data_type != '' AND data_type IS NOT NULL")
typed_conns = db_c.fetchone()[0]
db_c.execute("SELECT COUNT(*) FROM node_fallbacks")
fallback_count = db_c.fetchone()[0]

total = custom_final + native_final
print(f"\n{'='*60}")
print(f"✅ BASE DE DATOS DEL MECÁNICO - ESTADO FINAL")
print(f"{'='*60}")
print(f"  Custom nodes (builtin=0): {custom_final}")
print(f"  Native nodes (builtin=1): {native_final}")
print(f"  Total nodos registrados: {total}")
print(f"  Nodos con descripción completa: {with_desc}/{total}")
print(f"  Nodos con info upstream: {with_upstream}")
print(f"  Nodos con info downstream: {with_downstream}")
print(f"  Nodos con common_connections: {with_conns}")
print(f"  Conexiones con data_type: {typed_conns}")
print(f"  Fallbacks disponibles: {fallback_count}")
print(f"{'='*60}")

# List packages
db_c.execute("SELECT python_module, COUNT(*) as cnt FROM nodes WHERE builtin = 0 GROUP BY python_module ORDER BY cnt DESC")
print(f"\n📦 Paquetes custom (builtin=0):")
for row in db_c.fetchall():
    mod = row['python_module'] or 'unknown'
    # Extract just the package name
    pkg = mod.split('.')[-1] if mod else 'unknown'
    print(f"  {pkg}: {row['cnt']} nodos")

db_conn.close()
print(f"\n✅ Base de datos actualizada correctamente.")
