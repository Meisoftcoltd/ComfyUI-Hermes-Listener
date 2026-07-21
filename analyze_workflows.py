import json
import os
from collections import defaultdict, Counter

workflows_dir = "/home/meisoft/ComfyUI/user/default/workflows"
all_node_types = Counter()
workflow_details = []

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

    # Extraer nodos
    nodes = wf.get('nodes', [])
    if not nodes:
        print(f"  {fname}: 0 nodos")
        continue

    node_types_in_wf = []
    for node in nodes:
        if isinstance(node, dict) and 'class_type' in node:
            ct = node['class_type']
            all_node_types[ct] += 1
            node_types_in_wf.append(ct)

    # Extraer info del workflow
    revision = wf.get('revision', '')
    extra = wf.get('extra', {})
    author = extra.get('author', 'unknown') if isinstance(extra, dict) else 'unknown'

    workflow_details.append({
        'name': fname,
        'node_count': len([n for n in nodes if isinstance(n, dict) and 'class_type' in n]),
        'unique_types': len(set(node_types_in_wf)),
        'types': list(set(node_types_in_wf)),
        'author': author,
        'revision': revision
    })

# Mostrar analisis
print(f"{'='*80}")
print(f"ANALISIS DE {len(workflow_details)} WORKFLOWS")
print(f"{'='*80}")

# Top nodos más usados
print(f"\nTOP 20 NODOS MAS USADOS:")
for ct, count in all_node_types.most_common(20):
    print(f"  {ct}: {count} veces")

# Agrupar por categoria
print(f"\n{'='*80}")
print("WORKFLOWS POR CATEGORIA:")
print(f"{'='*80}")

# Identificar nodos principales por cada workflow
main_nodes = {}
for wd in workflow_details:
    for t in wd['types']:
        if 'Loader' in t or 'Checkpoint' in t or 'UNET' in t or 'clip' in t.lower():
            if t not in main_nodes:
                main_nodes[t] = []
            main_nodes[t].append(wd['name'])
            break

for loader, wfs in sorted(main_nodes.items()):
    print(f"\n  [{loader}]:")
    for w in wfs:
        print(f"    - {w}")

# Categorías por nombre
print(f"\n{'='*80}")
print("CATEGORIZACION POR NOMBRE DE WORKFLOW:")
print(f"{'='*80}")

categories = defaultdict(list)
for wd in workflow_details:
    name = wd['name'].lower()
    if 'flux' in name:
        categories['FLUX'].append(wd['name'])
    elif 'wan' in name or 'video' in name:
        categories['VIDEO/WAN'].append(wd['name'])
    elif 'scail' in name or 'pose' in name or 'dwpose' in name:
        categories['POSE/SCAIL'].append(wd['name'])
    elif 'character' in name or 'swapping' in name or 'reactor' in name:
        categories['CHARACTER/SWAP'].append(wd['name'])
    elif 'horoscopo' in name or 'prediccion' in name or 'sinastria' in name:
        categories['HORO/ASTRO'].append(wd['name'])
    elif 'upscaler' in name or 'subtitle' in name or 'subtitulo' in name:
        categories['UPSCALER/TEXTO'].append(wd['name'])
    elif 'fish' in name or 'tts' in name or 'speech' in name:
        categories['TE/TTs/VOZ'].append(wd['name'])
    elif 'guion' in name or 'gemini' in name:
        categories['GUION/TEMA'].append(wd['name'])
    elif 'pulso' in name or 'sefir' in name or 'vibr' in name:
        categories['ESPIRITUAL'].append(wd['name'])
    elif 'reporte' in name:
        categories['REPORTE/ANALISIS'].append(wd['name'])
    elif 'multi' in name:
        categories['BATCH/MULTI'].append(wd['name'])
    else:
        categories['OTROS'].append(wd['name'])

for cat, wfs in sorted(categories.items()):
    print(f"\n  [{cat}] ({len(wfs)} workflows):")
    for w in wfs:
        print(f"    - {w}")

# Resumen general
print(f"\n{'='*80}")
print("RESUMEN:")
print(f"{'='*80}")
print(f"  Total workflows: {len(workflow_details)}")
print(f"  Nodos únicos en total: {len(all_node_types)}")
print(f"  Categorías identificadas: {len(categories)}")

# Guardar
with open('/home/meisoft/ComfyUI-Hermes-Listener/workflows_analysis.json', 'w') as f:
    json.dump({
        'total_workflows': len(workflow_details),
        'total_unique_nodes': len(all_node_types),
        'node_frequency': dict(all_node_types.most_common()),
        'by_category': dict(categories),
        'by_loader': {k: v for k, v in main_nodes.items()},
        'workflows': [
            {
                'name': wd['name'],
                'node_count': wd['node_count'],
                'unique_types': wd['unique_types'],
                'types': wd['types'],
                'author': wd['author']
            }
            for wd in workflow_details
        ]
    }, f, indent=2, ensure_ascii=False)
    print(f"\n  Analisis guardado en workflows_analysis.json")
