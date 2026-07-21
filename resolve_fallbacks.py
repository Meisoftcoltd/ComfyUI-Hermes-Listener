#!/usr/bin/env python3
"""
Motor de resolución de nodos faltantes en workflows ComfyUI.

Consulta la base de datos directamente para saber qué encoder corresponde a cada modelo.
Si el encoder del workflow es `DualCLIPEncode` pero no está instalado,
busca en la tabla `node_fallbacks` un respaldo disponible.

Uso: python3 resolve_fallbacks.py /ruta/al/workflow.json

Flujo:
  1. Detectar qué modelo de difusión usa el workflow
  2. Consultar la BD para ver qué tipo de encoder requiere
  3. Si el workflow usa un encoder que no existe, reemplazar por el de la BD
  4. Si falta un nodo, buscar en node_fallbacks
"""

import sqlite3
import json
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "comfyui_nodes.db"


def get_model_from_workflow(workflow_json):
    """
    Extrae el nombre del modelo de difusión del workflow.
    Busca en CheckpointLoaderSimple o UNETLoader.
    Devuelve el nombre del archivo (sin ruta).
    """
    prompt = workflow_json.get("prompt", {})

    for node_id, node_data in prompt.items():
        if not isinstance(node_data, dict):
            continue

        class_type = node_data.get('class_type', '')
        inputs = node_data.get('inputs', {})

        if class_type in ('CheckpointLoaderSimple',):
            ckpt = inputs.get('ckpt_name', '')
            if '/' in ckpt:
                ckpt = ckpt.split('/')[-1]
            return ckpt

        elif class_type in ('UNETLoader', 'UNETLoaderExtended'):
            unet = inputs.get('unet_name', '')
            if '/' in unet:
                unet = unet.split('/')[-1]
            return unet

    return None


def get_encoder_for_model(model_name):
    """
    Consulta la BD para saber qué encoder corresponde a un modelo.
    Devuelve dict con: { 'encoder_type', 'encoder_file', 'encoder_node_type' }
    """
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()

    # Primero buscar por nombre exacto
    c.execute('''
        SELECT type, recommended_encoder, encoder_path
        FROM models
        WHERE name = ?
        LIMIT 1
    ''', (model_name,))
    row = c.fetchone()

    if row and row[1]:
        # Encontrar el archivo del encoder en la BD para saber su tipo
        c.execute('''
            SELECT type FROM models WHERE name = ?
        ''', (row[2],))
        enc_row = c.fetchone()

        encoder_type = row[1]
        if enc_row:
            encoder_type = enc_row[0]

        db.close()
        return {
            'encoder_type': encoder_type,
            'encoder_file': row[2],
        }

    db.close()
    return None


def get_encoder_node_type(encoder_type):
    """
    Mapea tipo de encoder a class_type del nodo correspondiente.
    """
    map = {
        'CLIP_QWEN': 'CLIPTextEncode',
        'CLIP_UMT5': 'CLIPTextEncode',
        'CLIP_TEXT_PROJECTION': 'CLIPTextEncode',
        'CLIP_VISION': 'CLIPVisionEncode',
        'CLIP_GEMMA': 'CLIPTextEncode',
        'CLIP_TEXT_PROJECTION': 'CLIPTextEncode',
    }
    return map.get(encoder_type, 'CLIPTextEncode')


def get_installed_nodes(db_path=DB_PATH):
    """Obtiene lista de nodos instalados (desde tabla 'nodes' de la DB)."""
    db = sqlite3.connect(db_path)
    c = db.cursor()
    c.execute('SELECT class_type FROM nodes')
    installed = set(row[0] for row in c.fetchall())
    db.close()
    return installed


def resolve_workflow(workflow_json):
    """
    Resuelve nodos faltantes en un workflow consultando la BD.

    Returns: (workflow_corregido, lista_de_cambios)
    """
    prompt = workflow_json.get("prompt", {})
    if not prompt:
        return workflow_json, []

    changes = []

    # 1. Identificar el modelo del workflow
    model_name = get_model_from_workflow(workflow_json)
    encoder_info = get_encoder_for_model(model_name) if model_name else None

    if encoder_info:
        encoder_type = encoder_info['encoder_type']
        encoder_node_type = get_encoder_node_type(encoder_type)

        print(f"\n📌 Modelo: {model_name}")
        print(f"   Encoder (tipo): {encoder_type}")
        print(f"   Encoder (archivo): {encoder_info['encoder_file']}")
        print(f"   Nodo esperado: {encoder_node_type}")

        # 2. Buscar nodos encoder en el workflow y verificar que coinciden
        for node_id, node_data in list(prompt.items()):
            if not isinstance(node_data, dict) or 'class_type' not in node_data:
                continue

            class_type = node_data.get('class_type', '')

            # Si es un nodo de encoder pero no es el esperado para este modelo
            if 'Encode' in class_type and class_type != encoder_node_type:
                # Buscar fallback en la BD
                db = sqlite3.connect(DB_PATH)
                c = db.cursor()
                c.execute('''
                    SELECT fallback_node, condition
                    FROM node_fallbacks
                    WHERE required_node = ?
                ''', (class_type,))
                fb_row = c.fetchone()
                db.close()

                if fb_row and fb_row[0] == encoder_node_type:
                    old_type = prompt[node_id]['class_type']
                    prompt[node_id]['class_type'] = encoder_node_type
                    changes.append({
                        'node_id': node_id,
                        'from': old_type,
                        'to': encoder_node_type,
                        'reason': fb_row[1] if fb_row[1] else 'Reemplazo por encoder esperado'
                    })

    workflow_json["prompt"] = prompt
    return workflow_json, changes


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            workflow = json.load(f)
    else:
        workflow = json.load(sys.stdin)

    print("=" * 60)
    print("🔍 RESOLVEDOR DE NODOS (BD dinámica)")
    print("=" * 60)

    resolved, changes = resolve_workflow(workflow)

    if changes:
        print(f"\n✅ {len(changes)} cambio(s) realizados:")
        for c in changes:
            print(f"  Nodo {c['node_id']}: {c['from']} → {c['to']}")
            print(f"    → {c['reason']}")
    else:
        print("\n✅ Todo correcto, sin cambios")

    print("\n" + "=" * 60)
    print("WORKFLOW CORREGIDO:")
    print("=" * 60)
    print(json.dumps(resolved, indent=2))


if __name__ == '__main__':
    main()
