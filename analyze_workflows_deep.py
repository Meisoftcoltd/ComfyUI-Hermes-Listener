import json
import os
from collections import defaultdict, Counter

workflows_dir = "/home/meisoft/ComfyUI/user/default/workflows"
all_node_types = Counter()
workflow_details = []
models_used = {}  # model_name -> set of workflow_names
loras_used = {}   # lora_name -> set of workflow_names

# Patrones para identificar tipos de LoRA/Checkpoints
LORA_PATTERNS = {
    'cambio_estilo': ['style', 'artistic', 'watercolor', 'oil', 'node', 'sketch', 'comic', 'anime', 'cartoon'],
    'personaje': ['character', 'face', 'portrait', 'head', 'body'],
    'destilacion': ['distill', 'distilled', 'small', 'turbo', 'fast', 'step_lora'],
    'control': ['control', 'depth', 'normal', 'canny', 'openpose', 'dwpose'],
    'textura': ['texture', 'pattern', 'fabric', 'wood', 'stone'],
    'iluminacion': ['lighting', 'shadow', 'glow', 'neon', 'golden'],
    'modelo_base': ['checkpoint', 'model', 'base', 'core', 'main'],
}

def detect_model_type(filename):
    """Detecta el tipo de modelo según su nombre."""
    fn = filename.lower()
    
    if 'flux' in fn or 'klo' in fn:
        return 'FLUX'
    elif 'sdxl' in fn or 'sd15' in fn or '1.5' in fn:
        return 'STABLE_DIFFUSION'
    elif 'wan' in fn or 'wan2' in fn:
        return 'WAN'
    elif 'lora' in fn:
        return 'LoRA'
    elif 'checkpoint' in fn or 'safetensors' in fn:
        return 'CHECKPOINT'
    elif 'vae' in fn:
        return 'VAE'
    elif 'clip' in fn:
        return 'CLIP'
    elif 'control' in fn or 'controlnet' in fn:
        return 'CONTROLNET'
    else:
        return 'OTHER'

def detect_lora_purpose(filename):
    """Detecta el propósito de un LoRA."""
    fn = filename.lower()
    for purpose, patterns in LORA_PATTERNS.items():
        for pattern in patterns:
            if pattern in fn:
                return purpose
    return 'GENERIC'

def get_workflow_purpose(name):
    """Infiere el propósito del workflow desde su nombre."""
    name_lower = name.lower()
    
    if 'horoscopo' in name_lower or 'prediccion' in name_lower:
        return 'HORO/ASTROLOGIA'
    elif 'character' in name_lower or 'swapping' in name_lower or 'reactor' in name_lower:
        return 'CARACTER/INTERCAMBIO'
    elif 'video' in name_lower or 'wan' in name_lower:
        return 'VIDEO/ANIMACION'
    elif 'upscaler' in name_lower or 'subtitle' in name_lower:
        return 'UPSCALER/TEXTO'
    elif 'fish' in name_lower or 'tts' in name_lower:
        return 'TEXTO_A_VOZ'
    elif 'guion' in name_lower or 'gemini' in name_lower:
        return 'GUI/LLM'
    elif 'pulso' in name_lower or 'sefir' in name_lower or 'vibr' in name_lower:
        return 'ESPIRITUAL/PUlSO'
    elif 'reporte' in name_lower:
        return 'REPORTE/ANALISIS'
    elif 'multi' in name_lower or 'batch' in name_lower:
        return 'BATCH/MULTI-PROC'
    else:
        return 'GENERAL'

def build_node_map(nodes):
    """Construye un mapa de node_id -> node_dict."""
    node_map = {}
    for node in nodes:
        if isinstance(node, dict) and 'id' in node:
            node_map[node['id']] = node
    return node_map

def resolve_value_recursive(link_id, node_map, visited=None):
    """
    Resuelve un link_id a un valor literal (cadena) recorriendo el grafo de conexiones.
    Para cada link: [source_node_id, source_output_index, ...]
    Encuentra el nodo fuente, busca su output, y si tiene valor devuelvelo, 
    si tiene otro link, lo resuelve recursivamente.
    """
    if visited is None:
        visited = set()
    if link_id in visited:
        return None  # ciclo detectado
    visited.add(link_id)
    
    # El link es un array: [source_node_id, source_output_index, ...]
    if not isinstance(link_id, list) or len(link_id) < 2:
        return None
    
    source_node_id = link_id[0]
    source_output_idx = link_id[1]
    
    source_node = node_map.get(source_node_id)
    if not isinstance(source_node, dict):
        return None
    
    # Buscar el output en el nodo fuente
    outputs = source_node.get('outputs', [])
    if not isinstance(outputs, list) or source_output_idx >= len(outputs):
        return None
    
    source_output = outputs[source_output_idx]
    if not isinstance(source_output, dict):
        return None
    
    # Verificar si el output tiene un valor directo
    if 'value' in source_output and source_output['value'] is not None:
        val = source_output['value']
        if isinstance(val, str):
            return val
        return None
    
    # Si el output tiene links, tomar el primero y resolver recursivamente
    out_links = source_output.get('links')
    if isinstance(out_links, list) and len(out_links) > 0:
        first_link_id = out_links[0]
        if isinstance(first_link_id, list):
            return resolve_value_recursive(first_link_id, node_map, visited)
        elif isinstance(first_link_id, int):
            # El link_id es un entero (link_id)
            # Encontrar qué nodo usa este link como input
            for node_id, node in node_map.items():
                if not isinstance(node, dict):
                    continue
                inputs = node.get('inputs', [])
                if not isinstance(inputs, list):
                    continue
                for inp in inputs:
                    if isinstance(inp, dict) and inp.get('link') == first_link_id:
                        # Este nodo recibe el link, si tiene value, devolverlo
                        if 'value' in inp and isinstance(inp['value'], str):
                            return inp['value']
                        # Si tiene otro link, resolver recursivamente
                        if 'link' in inp and isinstance(inp.get('link'), list):
                            return resolve_value_recursive(inp['link'], node_map, visited)
    
    return None

def find_model_in_inputs(node, node_map, visited=None):
    """Busca modelos en los inputs de un nodo."""
    found = []
    if visited is None:
        visited = set()
    
    inputs = node.get('inputs', [])
    if not isinstance(inputs, list):
        return found
    
    for inp in inputs:
        if not isinstance(inp, dict):
            continue
        
        iname = inp.get('name', '')
        
        # Si tiene valor directo
        if 'value' in inp and inp['value'] is not None:
            val = inp['value']
            if isinstance(val, str) and len(val) > 4:
                if any(pat in val for pat in ['safetensors', 'ckpt', 'pth', 'lora', 'gguf', 'bin']):
                    found.append({'path': val, 'input': iname, 'type': 'direct'})
            continue
        
        # Si tiene link, resolverlo
        if 'link' in inp and inp['link'] is not None:
            link = inp['link']
            # Si link es una lista [source_id, output_idx]
            if isinstance(link, list):
                val = resolve_value_recursive(link, node_map, visited)
                if val and isinstance(val, str) and len(val) > 4:
                    if any(pat in val for pat in ['safetensors', 'ckpt', 'pth', 'lora', 'gguf', 'bin']):
                        found.append({'path': val, 'input': iname, 'type': 'via_link'})
            # Si link es un int (link_id)
            elif isinstance(link, int):
                # Buscar qué nodo usa este link y tiene value
                for nid, nd in node_map.items():
                    if not isinstance(nd, dict):
                        continue
                    nd_inputs = nd.get('inputs', [])
                    if not isinstance(nd_inputs, list):
                        continue
                    for nd_inp in nd_inputs:
                        if isinstance(nd_inp, dict) and nd_inp.get('link') == link:
                            if 'value' in nd_inp and isinstance(nd_inp['value'], str):
                                v = nd_inp['value']
                                if len(v) > 4 and any(pat in v for pat in ['safetensors', 'ckpt', 'pth', 'lora', 'gguf', 'bin']):
                                    found.append({'path': v, 'input': iname, 'type': 'via_link_id'})
    
    return found

for fname in sorted(os.listdir(workflows_dir)):
    if not fname.endswith(".json"):
        continue
    fpath = os.path.join(workflows_dir, fname)
    try:
        with open(fpath, 'r') as f:
            wf = json.load(f)
    except Exception as e:
        print(f"  {fname}: ERROR - {e}")
        continue

    # Validar que wf sea un dict con 'nodes'
    if not isinstance(wf, dict):
        print(f"  {fname}: SKIPPED (not a dict, type={type(wf).__name__})")
        continue
    
    nodes = wf.get('nodes', [])
    
    if not isinstance(nodes, list) or len(nodes) == 0:
        print(f"  {fname}: 0 nodos")
        continue

    node_types_in_wf = []
    models_in_wf = []
    loras_in_wf = []
    
    # Construir mapa de nodos
    node_map = build_node_map(nodes)
    
    for node in nodes:
        if not isinstance(node, dict):
            continue
        ct = node.get('class_type') or node.get('type', '')
        if not ct:
            continue
        
        all_node_types[ct] += 1
        node_types_in_wf.append(ct)
        
        # Determinar si este nodo es un loader (puede tener model_path en inputs)
        is_loader = any(kw in ct.lower() for kw in ['loader', 'checkpoint', 'unet', 'clip', 'vae', 'lora'])
        
        if is_loader:
            # Buscar modelos en los inputs de este nodo
            found_models = find_model_in_inputs(node, node_map)
            for m in found_models:
                model_path = m['path']
                model_type = detect_model_type(model_path)
                models_in_wf.append({'path': model_path, 'type': model_type, 'node': ct, 'input': m['input'], 'source': m['type']})
                
                if model_path not in models_used:
                    models_used[model_path] = set()
                models_used[model_path].add(fname)
                
                # Si es un LoRA, registrar su propósito
                if 'lora' in model_path.lower():
                    lora_purpose = detect_lora_purpose(model_path)
                    loras_in_wf.append({'name': model_path, 'purpose': lora_purpose})
                    if model_path not in loras_used:
                        loras_used[model_path] = {'purpose': lora_purpose, 'workflows': set()}
                    loras_used[model_path]['workflows'].add(fname)
    
    # Extraer info del workflow
    revision = wf.get('revision', '')
    extra = wf.get('extra', {})
    author = extra.get('author', 'unknown') if isinstance(extra, dict) else 'unknown'
    workflow_purpose = get_workflow_purpose(fname)
    
    workflow_details.append({
        'name': fname,
        'node_count': len([n for n in nodes if isinstance(n, dict) and (n.get('class_type') or n.get('type', ''))]),
        'unique_types': len(set(node_types_in_wf)),
        'types': list(set(node_types_in_wf)),
        'models': models_in_wf,
        'loras': loras_in_wf,
        'purpose': workflow_purpose,
        'author': author,
        'revision': revision
    })

# Mostrar analisis profundo
print(f"{'='*80}")
print(f"ANALISIS PROFUNDO DE {len(workflow_details)} WORKFLOWS")
print(f"{'='*80}")

# Top nodos más usados
print(f"\n{'='*60}")
print("TOP 30 NODOS MAS USADOS:")
print(f"{'='*60}")
for ct, count in all_node_types.most_common(30):
    print(f"  {ct}: {count} veces")

# Modelos usados por workflow
print(f"\n{'='*80}")
print("MODELOS USADOS:")
print(f"{'='*80}")
for model, wfs in models_used.items():
    model_type = detect_model_type(model)
    print(f"\n  [{model_type}] {model}:")
    for w in wfs:
        print(f"    - {w}")

# LoRAs usados con su propósito
print(f"\n{'='*80}")
print("LoRAs USADOS:")
print(f"{'='*80}")
for lora, info in loras_used.items():
    purpose = info['purpose']
    print(f"\n  {lora} (propósito: {purpose}):")
    for w in info['workflows']:
        print(f"    - {w}")

# Categorías por propósito del workflow
print(f"\n{'='*80}")
print("WORKFLOWS POR PROPÓSITO:")
print(f"{'='*80}")

purpose_categories = defaultdict(list)
for wd in workflow_details:
    purpose_categories[wd['purpose']].append(wd['name'])

for purpose, wfs in sorted(purpose_categories.items()):
    print(f"\n  [{purpose}] ({len(wfs)} workflows):")
    for w in wfs:
        print(f"    - {w}")

# Resumen general
print(f"\n{'='*80}")
print("RESUMEN:")
print(f"{'='*80}")
print(f"  Total workflows: {len(workflow_details)}")
print(f"  Nodos únicos en total: {len(all_node_types)}")
print(f"  Modelos detectados: {len(models_used)}")
print(f"  LoRAs detectados: {len(loras_used)}")
print(f"  Categorías de propósito: {len(purpose_categories)}")

# Guardar analisis profundo
with open('/home/meisoft/ComfyUI-Hermes-Listener/workflows_deep_analysis.json', 'w') as f:
    json.dump({
        'total_workflows': len(workflow_details),
        'total_unique_nodes': len(all_node_types),
        'node_frequency': dict(all_node_types.most_common()),
        'models': {k: list(v) for k, v in models_used.items()},
        'loras': {k: {**v, 'workflows': list(v['workflows'])} for k, v in loras_used.items()},
        'by_purpose': dict(purpose_categories),
        'by_category': dict(purpose_categories),
        'workflows': [
            {
                'name': wd['name'],
                'node_count': wd['node_count'],
                'unique_types': wd['unique_types'],
                'types': wd['types'],
                'models': wd['models'],
                'loras': wd['loras'],
                'purpose': wd['purpose'],
                'author': wd['author']
            }
            for wd in workflow_details
        ]
    }, f, indent=2, ensure_ascii=False)
    print(f"\n  Analisis guardado en workflows_deep_analysis.json")
