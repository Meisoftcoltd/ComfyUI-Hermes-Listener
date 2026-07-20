"""
comfyui_nodes.py — Base de datos SQLite para aprendizaje de nodos, conexiones y workflows.

Escanea /object_info de ComfyUI, mapea modelos del sistema de archivos,
registra conexiones descubiertas y analiza workflows para detectar nodos faltantes
o complementarios. El agente puede consultar la DB para aprender de errores y aciertos.

Uso:
    from nodes.comfyui_nodes import ComfyuiNodesDB, init_db, analyze_workflow
    
    # Inicializar y escanear
    init_db()
    
    # Analizar un workflow
    result = analyze_workflow("wf_001", workflow_json)
    print(result)

    # Consultar nodos compatibles
    db = ComfyuiNodesDB()
    compatibles = db.get_compatible_nodes("LATENT")
    
    # Obtener conexiones conocidas
    conexiones = db.get_node_connections("KSampler")
"""

import sqlite3
import os
import json
from datetime import datetime


# Rutas por defecto - se sobreescriben con ENV si está disponible
_DB_PATH = os.environ.get("COMFYUI_NODES_DB", "/home/meisoft/ComfyUI-Hermes-Listener/comfyui_nodes.db")
_COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8189")
_MODELS_DIR = os.environ.get("COMFYUI_MODELS_DIR", "/home/meisoft/ComfyUI/models")

# Mapeo de carpetas de modelos a tipos conocidos
MODEL_DIR_TO_TYPE = {
    "checkpoints": "checkpoint",
    "unet": "unet",
    "vae": "vae",
    "clip": "clip",
    "loras": "lora",
    "controlnet": "controlnet",
    "upscale_models": "upscale",
    "embeddings": "embedding",
    "insightface": "insightface",
    "hypernetworks": "hypernetwork",
    "photomaker": "photomaker",
    "ipadapter": "ipadapter",
    "gligen": "gligen",
    "instantid": "instantid",
}


class ComfyuiNodesDB:
    """
    Motor de base de datos SQLite para aprendizaje de nodos ComfyUI.
    
    Mantiene:
    - nodes: catálogo de nodos instalados (escaneado desde /object_info)
    - model_paths: modelos en disco con sus tipos y nodos compatibles
    - node_connections: conexiones válidas descubiertas entre nodos
    - workflow_analysis: historial de análisis de workflows
    - node_usage_stats: estadísticas de uso por nodo
    """

    def __init__(self, db_path=None, comfyui_url=None, models_dir=None):
        self.db_path = db_path or _DB_PATH
        self.comfyui_url = comfyui_url or _COMFYUI_URL
        self.models_dir = models_dir or _MODELS_DIR
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

    def _get_conn(self):
        """Conexión segura con WAL y foreign keys."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        """Crea las tablas si no existen."""
        conn = self._get_conn()
        c = conn.cursor()

        # ── nodes ──────────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                class_type TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                builtin INTEGER DEFAULT 0,
                input_params TEXT,     -- JSON array de {name, type, required, optional, default}
                output_params TEXT,    -- JSON array de {name, type, description}
                last_scanned TEXT      -- ISO timestamp
            )
        """)

        # ── model_paths ────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS model_paths (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                model_type TEXT,
                compatible_node_types TEXT,  -- JSON array de class_types
                last_scanned TEXT
            )
        """)

        # ── node_connections ───────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS node_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_class_type TEXT NOT NULL,
                source_param TEXT NOT NULL,
                target_class_type TEXT NOT NULL,
                target_param TEXT NOT NULL,
                data_type TEXT,
                is_valid INTEGER DEFAULT 1,
                registered_at TEXT,
                FOREIGN KEY(source_class_type) REFERENCES nodes(class_type),
                FOREIGN KEY(target_class_type) REFERENCES nodes(class_type)
            )
        """)

        # ── workflow_analysis ──────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS workflow_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                status TEXT NOT NULL,
                missing_nodes TEXT,         -- JSON array de class_types faltantes
                extra_nodes TEXT,           -- JSON array de class_types extra/no usados
                resolved_deps TEXT          -- JSON array de conexiones resueltas
            )
        """)

        # ── node_usage_stats ───────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS node_usage_stats (
                class_type TEXT PRIMARY KEY,
                last_used TEXT,
                usage_count INTEGER DEFAULT 0,
                FOREIGN KEY(class_type) REFERENCES nodes(class_type)
            )
        """)

        conn.commit()
        conn.close()

    # ─── ESCANEO ──────────────────────────────────────────────────────

    def scan_nodes(self):
        """Descarga /object_info de ComfyUI y actualiza la tabla nodes.
        
        Returns: {"status": "ok"|"error", "updated": int, "message": str}
        """
        import urllib.request
        import urllib.error

        url = f"{self.comfyui_url}/object_info"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                obj_info = json.loads(resp.read().decode())
        except Exception as e:
            return {"status": "error", "updated": 0, "message": f"Conexión fallida: {e}"}

        # Normalizar: /object_info devuelve un objeto donde cada clave es un class_type
        # Cada valor tiene: { "input": { "required": {}, "optional": {} }, "output": [...] }
        all_nodes = {}
        if isinstance(obj_info, dict):
            # Filtrar solo nodos (tienen campo 'input')
            for key, val in obj_info.items():
                if isinstance(val, dict) and "input" in val:
                    all_nodes[key] = val

        now = datetime.utcnow().isoformat()
        updated = 0

        conn = self._get_conn()
        c = conn.cursor()

        for class_type, info in all_nodes.items():
            inputs_raw = info.get("input", {})
            outputs_raw = info.get("output", [])

            # Normalizar inputs a array JSON: cada entrada = {name, type, required, default}
            input_list = []
            required = inputs_raw.get("required", {})
            optional = inputs_raw.get("optional", {})

            for name, spec in required.items():
                input_list.append({
                    "name": name,
                    "type": spec[0] if isinstance(spec, (list, tuple)) else str(spec),
                    "required": True,
                    "default": None
                })
            for name, spec in optional.items():
                input_list.append({
                    "name": name,
                    "type": spec[0] if isinstance(spec, (list, tuple)) else str(spec),
                    "required": False,
                    "default": spec[1] if isinstance(spec, (list, tuple)) and len(spec) > 1 else None
                })

            # Normalizar outputs a array JSON: cada entrada = {name, type}
            output_list = []
            if isinstance(outputs_raw, list):
                for out in outputs_raw:
                    if isinstance(out, (list, tuple)):
                        output_list.append({
                            "name": out[0] if out else "",
                            "type": out[1] if len(out) > 1 else ""
                        })
                    elif isinstance(out, dict):
                        output_list.append({
                            "name": out.get("name", ""),
                            "type": out.get("type", "")
                        })
                    elif isinstance(out, str):
                        output_list.append({"name": out, "type": out})

            name = info.get("name", class_type)
            desc = info.get("description", "")

            c.execute("""
                INSERT OR REPLACE INTO nodes
                (class_type, name, description, builtin, input_params, output_params, last_scanned)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                class_type, name, desc, 1,
                json.dumps(input_list), json.dumps(output_list), now
            ))
            updated += 1

        conn.commit()
        conn.close()
        return {"status": "ok", "updated": updated}

    def scan_models(self):
        """Escanea MODELS_DIR recursivamente y actualiza model_paths.
        
        Returns: {"status": "ok"|"error", "scanned": int, "message": str}
        """
        conn = self._get_conn()
        c = conn.cursor()
        now = datetime.utcnow().isoformat()
        extensions = ('.safetensors', '.pt', '.bin', '.pth', '.ckpt', '.onnx')

        scanned = 0
        for root, _dirs, files in os.walk(self.models_dir):
            for fname in files:
                if not fname.lower().endswith(extensions):
                    continue

                rel = os.path.relpath(root, self.models_dir)
                parent_dir = rel.split(os.sep)[0].lower() if rel != '.' else 'unknown'
                mtype = MODEL_DIR_TO_TYPE.get(parent_dir, 'other')
                fpath = os.path.join(root, fname)

                c.execute("""
                    INSERT OR REPLACE INTO model_paths
                    (name, file_path, model_type, compatible_node_types, last_scanned)
                    VALUES (?, ?, ?, ?, ?)
                """, (fname, fpath, mtype, json.dumps([]), now))
                scanned += 1

        conn.commit()
        conn.close()
        return {"status": "ok", "scanned": scanned}

    def _sync_models_to_nodes(self):
        """Conecta cada modelo con los class_types que pueden usarlo."""
        conn = self._get_conn()
        c = conn.cursor()

        # Obtener todos los modelos
        c.execute("SELECT id, name, model_type FROM model_paths")
        models = c.fetchall()

        # Obtener todos los nodos
        c.execute("SELECT class_type, input_params FROM nodes")
        nodes = c.fetchall()

        # Mapeo de tipo de modelo a nombres de input que los usan
        MODEL_INPUT_MAP = {
            "checkpoint": ["ckpt_name", "clip_name", "model_name", "unet_name"],
            "unet": ["unet_name", "model_name"],
            "vae": ["vae_name"],
            "clip": ["clip_name", "clip2_name"],
            "lora": ["lora_name"],
            "controlnet": ["controlnet_name", "model_name"],
            "upscale": ["upscale_model_name"],
            "embedding": ["embedding_name"],
            "ipadapter": ["ipadapter_name", "clip_vision_name"],
            "gligen": ["gligen_name"],
        }

        for model in models:
            mid, mname, mtype = model
            compatible = []

            if mtype in MODEL_INPUT_MAP:
                target_inputs = MODEL_INPUT_MAP[mtype]
                for node in nodes:
                    nclass, inputs_json = node
                    try:
                        inputs = json.loads(inputs_json) if inputs_json else []
                    except json.JSONDecodeError:
                        continue

                    for inp in inputs:
                        if isinstance(inp, dict) and inp.get("name") in target_inputs:
                            if nclass not in compatible:
                                compatible.append(nclass)
                            break

            if compatible:
                c.execute("UPDATE model_paths SET compatible_node_types = ? WHERE id = ?",
                          (json.dumps(compatible), mid))

        conn.commit()
        conn.close()
        return {"status": "ok", "synced": len(list(models))}

    # ─── ANÁLISIS DE WORKFLOW ───────────────────────────────────────

    def analyze_workflow(self, workflow_id, workflow_json):
        """
        Analiza un workflow JSON contra la DB.
        
        Detecta:
        - Nodos faltantes (no en la DB)
        - Conexiones válidas (ambos extremos en la DB)
        - Conexiones desconocidas (por aprender)
        - Nodos extras (en la DB pero no usados en el workflow)
        
        Returns: {
            "status": "ok"|"missing_nodes",
            "missing_nodes": [],
            "resolved_connections": int,
            "unknown_connections": [],
            "total_nodes": int
        }
        """
        conn = self._get_conn()
        c = conn.cursor()

        # Extraer nodos y conexiones del workflow
        used_nodes = {}
        connections = []

        for node_id, node_data in workflow_json.items():
            if not isinstance(node_data, dict):
                continue

            class_type = node_data.get("class_type", "")
            if not class_type:
                continue

            used_nodes[node_id] = class_type

            inputs = node_data.get("inputs", {})
            for param, value in inputs.items():
                if isinstance(value, list) and len(value) == 2:
                    source_id, _slot = value
                    source_node = workflow_json.get(str(source_id), {})
                    source_class = source_node.get("class_type", "")
                    if source_class:
                        connections.append({
                            "source_class_type": source_class,
                            "source_param": param,
                            "target_class_type": class_type,
                            "target_param": param
                        })

        # Obtener nodos conocidos
        c.execute("SELECT class_type FROM nodes")
        known_nodes = set(r["class_type"] for r in c.fetchall())

        missing = []
        for nid, class_type in used_nodes.items():
            if class_type not in known_nodes:
                if class_type not in missing:
                    missing.append(class_type)

        # Procesar conexiones
        resolved = []
        unknown_conns = []

        for conn_info in connections:
            src = conn_info["source_class_type"]
            tgt = conn_info["target_class_type"]

            if src in known_nodes and tgt in known_nodes:
                resolved.append(conn_info)
                c.execute("""
                    INSERT OR IGNORE INTO node_connections
                    (source_class_type, source_param, target_class_type, target_param, data_type, is_valid, registered_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                """, (
                    src, conn_info["source_param"],
                    tgt, conn_info["target_param"],
                    None,
                    datetime.utcnow().isoformat()
                ))
            elif src not in known_nodes or tgt not in known_nodes:
                unknown_conns.append(conn_info)

        conn.commit()

        # Registrar uso de cada nodo
        for class_type in used_nodes.values():
            c.execute("""
                INSERT INTO node_usage_stats (class_type, last_used, usage_count)
                VALUES (?, ?, 1)
                ON CONFLICT(class_type) DO UPDATE SET
                    last_used = excluded.last_used,
                    usage_count = usage_count + 1
            """, (class_type, datetime.utcnow().isoformat()))

        conn.commit()
        conn.close()

        # Guardar análisis
        status = "ok" if not missing else "missing_nodes"
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            INSERT INTO workflow_analysis
            (workflow_id, timestamp, status, missing_nodes, extra_nodes, resolved_deps)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            workflow_id, datetime.utcnow().isoformat(), status,
            json.dumps(missing), json.dumps([]), json.dumps(resolved)
        ))
        conn.commit()
        conn.close()

        return {
            "status": status,
            "missing_nodes": missing,
            "resolved_connections": len(resolved),
            "unknown_connections": unknown_conns,
            "total_nodes": len(used_nodes)
        }

    # ─── REGISTRO DE NODOS Y CONEXIONES ──────────────────────────────

    def register_node(self, class_type, name, description, input_params, output_params):
        """Añade manualmente un nodo no descubierto por /object_info."""
        conn = self._get_conn()
        c = conn.cursor()
        now = datetime.utcnow().isoformat()
        c.execute("""
            INSERT OR REPLACE INTO nodes
            (class_type, name, description, builtin, input_params, output_params, last_scanned)
            VALUES (?, ?, ?, 0, ?, ?, ?)
        """, (class_type, name, description, json.dumps(input_params), json.dumps(output_params), now))
        conn.commit()
        conn.close()
        return {"status": "ok"}

    def register_connection(self, source, source_param, target, target_param, data_type=None):
        """Registra una conexión válida entre nodos."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            INSERT OR IGNORE INTO node_connections
            (source_class_type, source_param, target_class_type, target_param, data_type, is_valid, registered_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
        """, (source, source_param, target, target_param, data_type, datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
        return {"status": "ok", "registered": c.rowcount > 0}

    def update_connection_type(self, source, source_param, target, target_param, data_type):
        """Actualiza el tipo de dato de una conexión ya existente."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            UPDATE node_connections
            SET data_type = ?
            WHERE source_class_type = ? AND source_param = ?
            AND target_class_type = ? AND target_param = ?
        """, (data_type, source, source_param, target, target_param))
        conn.commit()
        conn.close()
        return {"status": "ok", "updated": c.rowcount}

    # ─── CONSULTAS ────────────────────────────────────────────────────

    def get_compatible_nodes(self, output_type):
        """
        Devuelve nodos cuya salida contiene el tipo dado.
        
        Args:
            output_type: Tipo de dato (ej. "LATENT", "IMAGE", "CONDITIONING", "MODEL")
        
        Returns: lista de dicts con class_type, name, output_details
        """
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT class_type, name, description, output_params FROM nodes")

        compatible = []
        for row in c.fetchall():
            outputs = json.loads(row["output_params"] or "[]")
            for out in outputs:
                if isinstance(out, dict) and out.get("type") == output_type:
                    compatible.append({
                        "class_type": row["class_type"],
                        "name": row["name"],
                        "description": row["description"],
                        "output_details": out
                    })
                    break

        conn.close()
        return compatible

    def get_compatible_inputs(self, class_type):
        """
        Devuelve los inputs de un nodo y sus tipos.
        
        Returns: lista de dicts con name, type, required, default
        """
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT input_params FROM nodes WHERE class_type = ?", (class_type,))
        row = c.fetchone()
        conn.close()

        if not row:
            return []

        inputs = json.loads(row["input_params"] or "[]")
        return inputs

    def get_node_connections(self, class_type):
        """
        Devuelve todas las conexiones conocidas que involucran un nodo (como origen o destino).
        
        Returns: lista de dicts con source, source_param, target, target_param, type
        """
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT source_class_type, source_param, target_class_type, target_param, data_type
            FROM node_connections
            WHERE (source_class_type = ? OR target_class_type = ?)
            AND is_valid = 1
        """, (class_type, class_type))

        rows = c.fetchall()
        conn.close()

        return [
            {
                "source": r[0],
                "source_param": r[1],
                "target": r[2],
                "target_param": r[3],
                "type": r[4]
            }
            for r in rows
        ]

    def get_all_connections(self, limit=100):
        """Devuelve todas las conexiones registradas (para debug)."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT source_class_type, source_param, target_class_type, target_param, data_type, registered_at
            FROM node_connections
            WHERE is_valid = 1
            ORDER BY registered_at DESC
            LIMIT ?
        """, (limit,))

        rows = c.fetchall()
        conn.close()

        return [
            {
                "source": r[0],
                "source_param": r[1],
                "target": r[2],
                "target_param": r[3],
                "type": r[4],
                "registered_at": r[5]
            }
            for r in rows
        ]

    def get_missing_nodes_history(self, limit=10):
        """Últimos fallos de nodos faltantes detectados."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT workflow_id, timestamp, missing_nodes
            FROM workflow_analysis
            WHERE status = 'missing_nodes'
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))

        rows = c.fetchall()
        conn.close()

        return [
            {
                "workflow_id": r[0],
                "timestamp": r[1],
                "missing": json.loads(r[2]) if r[2] else []
            }
            for r in rows
        ]

    def get_model_by_name(self, name_pattern):
        """Busca modelos por patrón de nombre (fuzzy)."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT name, file_path, model_type, compatible_node_types
            FROM model_paths
            WHERE name LIKE ?
            LIMIT 20
        """, (f"%{name_pattern}%",))

        rows = c.fetchall()
        conn.close()

        return [
            {
                "name": r[0],
                "file_path": r[1],
                "model_type": r[2],
                "compatible_node_types": json.loads(r[3]) if r[3] else []
            }
            for r in rows
        ]

    def get_model_by_type(self, model_type):
        """Lista todos los modelos de un tipo dado."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT name, file_path, model_type, compatible_node_types
            FROM model_paths
            WHERE model_type = ?
        """, (model_type,))

        rows = c.fetchall()
        conn.close()

        return [
            {
                "name": r[0],
                "mark": r[1],
                "model_type": r[2],
                "compatible_node_types": json.loads(r[3]) if r[3] else []
            }
            for r in rows
        ]

    def get_usage_stats(self, limit=20):
        """Estadísticas de uso de nodos (los más usados)."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT class_type, last_used, usage_count
            FROM node_usage_stats
            ORDER BY usage_count DESC
            LIMIT ?
        """, (limit,))

        rows = c.fetchall()
        conn.close()

        return [
            {
                "class_type": r[0],
                "last_used": r[1],
                "usage_count": r[2]
            }
            for r in rows
        ]

    def get_stats(self):
        """Resumen de toda la base de datos."""
        conn = self._get_conn()
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM nodes")
        nodes_count = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM model_paths")
        models_count = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM node_connections")
        conns_count = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM workflow_analysis")
        wfs_count = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM node_usage_stats")
        usage_count = c.fetchone()[0]

        conn.close()

        return {
            "nodes_registered": nodes_count,
            "models_scanned": models_count,
            "connections_registered": conns_count,
            "workflows_analyzed": wfs_count,
            "nodes_tracked": usage_count
        }

    def clear_data(self, keep_nodes=False):
        """
        Limpia datos históricos manteniendo nodes (si keep_nodes=True).
        
        Útil para reiniciar el aprendizaje.
        """
        conn = self._get_conn()
        c = conn.cursor()

        if not keep_nodes:
            c.execute("DELETE FROM node_usage_stats")
            c.execute("DELETE FROM workflow_analysis")
            c.execute("DELETE FROM node_connections")

        c.execute("DELETE FROM model_paths")

        conn.commit()
        conn.close()
        return {"status": "ok"}


# ─── API de conveniencia para el agente ───────────────────────────────

def init_db():
    """
    Inicializa la base de datos y realiza el primer escaneo completo.
    
    Returns: {
        "nodes": {"status": "...", "updated": int},
        "models": {"status": "...", "scanned": int},
        "sync": {"status": "...", "synced": int}
    }
    """
    db = ComfyuiNodesDB()
    nodes_result = db.scan_nodes()
    models_result = db.scan_models()
    sync_result = db._sync_models_to_nodes()
    return {
        "nodes": nodes_result,
        "models": models_result,
        "sync": sync_result
    }


def analyze_workflow(wf_id, wf_json):
    """Analiza un workflow y devuelve estado, nodos faltantes y conexiones."""
    db = ComfyuiNodesDB()
    return db.analyze_workflow(wf_id, wf_json)


def get_compatible_nodes(output_type):
    """Busca nodos que producen un tipo de dato específico."""
    db = ComfyuiNodesDB()
    return db.get_compatible_nodes(output_type)


def get_node_connections(class_type):
    """Obtiene conexiones conocidas para un nodo."""
    db = ComfyuiNodesDB()
    return db.get_node_connections(class_type)


def get_db_stats():
    """Devuelve estadísticas de la base de datos."""
    db = ComfyuiNodesDB()
    return db.get_stats()
