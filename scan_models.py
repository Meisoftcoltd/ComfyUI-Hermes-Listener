"""
Escanea todos los modelos en el directorio de ComfyUI y crea un inventario
con rutas absolutas, tipo de modelo, y URL de descarga (si corresponde).
"""
import os
import sqlite3
import json
from pathlib import Path
from collections import defaultdict

MODELS_DIR = "/home/meisoft/ComfyUI/models"
DB_PATH = "/home/meisoft/ComfyUI-Hermes-Listener/comfyui_nodes.db"

# Mapeo de tipo -> URL de descarga (patrones comunes de HuggingFace)
HF_MODELS = {
    # FLUX
    'flux-2-klein-9b': 'https://huggingface.co/black-forest-labs/FLUX.2-Fill/resolve/main/flux2-klein-9b.safetensors',
    'flux-2-klein-4b': 'https://huggingface.co/black-forest-labs/FLUX.2-Fill/resolve/main/flux2-klein-4b.safetensors',
    'flux-2-klein-base-9b': 'https://huggingface.co/black-forest-labs/FLUX.2-Fill/resolve/main/flux-2-klein-base-9b.safetensors',
    # WAN
    'Wan2_1-I2V-14B-480P': 'https://huggingface.co/Wan-AI/Wan2.1-I2V-14B-480P/resolve/main/Wan2_1-I2V-14B-480P_fp8_e4m3fn.safetensors',
    'Wan2_1-InfiniTetalk-Single': 'https://huggingface.co/XiaoBaoYuanWu/Wan2.1-InfiniTetalk/resolve/main/Wan2_1-InfiniTetalk-Single_fp16.safetensors',
    # LTX
    'ltx-2.3-22b': 'https://huggingface.co/Lightricks/LTX-2.3/resolve/main/ltx-2.3-22b-distilled-1.1_transformer_only_fp8_scaled.safetensors',
    'ltx2.3': 'https://huggingface.co/Lightricks/LTX-2.3/resolve/main/ltx2.3.safetensors',
    # Krea
    'krea2_turbo': 'https://huggingface.co/KreaAI/Krea-2-Turbo/resolve/main/krea2_turbo_fp8.safetensors',
    'krea2_raw': 'https://huggingface.co/KreaAI/Krea-2-Turbo/resolve/main/krea2_raw_fp8_scaled.safetensors',
    # VAEs
    'flux2-vae': 'https://huggingface.co/black-forest-labs/FLUX.2-Fill/resolve/main/flux2-vae.safetensors',
    'taeltx2_3': 'https://huggingface.co/Lightricks/LTX-2.3/resolve/main/taeltx2_3.safetensors',
    'Wan2_1_VAE_bf16': 'https://huggingface.co/Wan-AI/Wan2.1-VGG/resolve/main/Wan2_1_VAE_bf16.safetensors',
    'LTX23_audio_vae': 'https://huggingface.co/Lightricks/LTX-2.3/resolve/main/LTX23_audio_vae_bf16.safetensors',
    'LTX23_video_vae': 'https://huggingface.co/Lightricks/LTX-2.3/resolve/main/LTX23_video_vae_bf16.safetensors',
    # CLIPs
    'clip_vision_h': 'https://huggingface.co/laion/CLIP-ViT-H-14-laion2B-s32B-b79K/resolve/main/clip_vision_h.safetensors',
    'umt5-xxl': 'https://huggingface.co/google/umt5-xxl/resolve/main/umt5-xxl-enc-bf16.safetensors',
    'qwen3vl_4b': 'https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct/resolve/main/qwen3vl_4b_fp8_scaled.safetensors',
    'gemma_3_12b': 'https://huggingface.co/google/gemma-3-12b-it/resolve/main/gemma_3_12B_it_fp8_scaled.safetensors',
    'qwen_3_8b': 'https://huggingface.co/Qwen/Qwen3-8B/resolve/main/qwen_3_8b_fp8mixed.safetensors',
    'ltx-2.3_text_projection': 'https://huggingface.co/Lightricks/LTX-2.3/resolve/main/ltx-2.3_text_projection_bf16.safetensors',
    # UNETs GGUF
    'ltx-2.3-22b-dev': 'https://huggingface.co/mindskip/LTX-2.3-GGUF/resolve/main/ltx-2.3-22b-dev-Q8_0.gguf',
    'flux-2-klein-9b-Q8': 'https://huggingface.co/Linoyuxian/FLUX.2-Klein-GGUF/resolve/main/flux-2-klein-9b-Q8_0.gguf',
    'Krea-2-Turbo-Q4': 'https://huggingface.co/KreaAI/Krea-2-Turbo/resolve/main/Krea-2-Turbo-Q4_K_M.gguf',
    # Textos GGUF
    'Qwen3-8B-Q8': 'https://huggingface.co/Qwen/Qwen3-8B/resolve/main/Qwen3-8B-Q8_0.gguf',
    'Qwen3-8B-Q6': 'https://huggingface.co/Qwen/Qwen3-8B/resolve/main/Qwen3-8B-Q6_K.gguf',
    # LoRAs
    'ltx-2-19b-lora-camera-control': 'https://huggingface.co/Lightricks/LTX-2.3/resolve/main/ltx-2-19b-lora-camera-control-dolly-right.safetensors',
    'lightx2v_I2V': 'https://huggingface.co/Lightricks/LightX2V/resolve/main/lightx2v_I2V_14B_480P_cfg_step_distill_rank64_bf16.safetensors',
    'ltx2.3_audio_reactive': 'https://huggingface.co/Lightricks/LTX-2.3/resolve/main/ltx2.3_audio_reactive_lora_v2.safetensors',
    'ltx2.3_upscale_ic': 'https://huggingface.co/Lightricks/LTX-2.3/resolve/main/ltx2.3_upscale_ic-lora_06250.safetensors',
    'ltx-2.3-22b-distilled': 'https://huggingface.co/Lightricks/LTX-2.3/resolve/main/ltx-2.3-22b-distilled-1.1_lora-dynamic_fro09_avg_rank_111_bf16.safetensors',
    # Otros
    'ESRGAN_4x': 'https://huggingface.co/opencv/ESRGAN_4x/resolve/main/ESRGAN_4x.pth',
    'MelBandRoformer': 'https://huggingface.co/epignatona/MelBandRoformer/resolve/main/MelBandRoformer_fp16.safetensors',
    'GFPGANv1.3': 'https://huggingface.co/TencentARC/GFPGAN/resolve/main/GFPGANv1.3.pth',
}

# Categorías de LoRA por nombre
LORA_CATEGORIES = {
    'camera_control': ['camera-control', 'dolly', 'jib', 'tilt', 'pan', 'zoom', 'static'],
    'motion_track': ['motion-track', 'tracking'],
    'detail': ['detailer', 'detail'],
    'cross-eyed': ['cross-eyed', 'eyes'],
    'decompression': ['decompression'],
    'hdr': ['hdr'],
    'lipdub': ['lipdub', 'lip'],
    'style': ['style', 'artistic', 'watercolor', 'oil', 'sketch', 'comic'],
    'character': ['character', 'face', 'portrait', 'head', 'body'],
    'distillation': ['distill', 'turbo', 'fast', 'step_lora'],
    'control': ['control', 'depth', 'normal', 'canny', 'openpose'],
    'audio': ['audio', 'reactive', 'music'],
    'upscale': ['upscale', 'spatial', 'detail'],
}

def detect_lora_category(filename):
    """Detecta la categoría de un LoRA por su nombre."""
    fn = filename.lower()
    for cat, patterns in LORA_CATEGORIES.items():
        for pat in patterns:
            if pat in fn:
                return cat
    return 'other'

def scan_models():
    """Escanea todos los modelos en el directorio de modelos."""
    models = []
    
    for root, dirs, files in os.walk(MODELS_DIR):
        for fname in files:
            fpath = os.path.join(root, fname)
            
            # Solo archivos de modelo conocidos
            if not any(fname.endswith(ext) for ext in ['.safetensors', '.ckpt', '.pt', '.bin', '.gguf', '.pth']):
                continue
            
            rel_path = os.path.relpath(fpath, '/home/meisoft/ComfyUI')
            
            # Determinar categoría por subdirectorio (SEGUNDO nivel del rel_path)
            parts = rel_path.split(os.sep)
            # parts[0] = 'models', parts[1] = 'diffusion_models'/'loras'/'vae'/etc.
            category_dir = parts[1] if len(parts) > 1 else 'unknown'
            
            # Detectar tipo de modelo
            model_type = classify_model(fname, category_dir)
            
            # Buscar URL de descarga
            download_url = find_download_url(fname)
            
            # Propósito para LoRAs
            lora_purpose = detect_lora_category(fname) if 'lora' in fname.lower() else None
            
            # Tamaño del archivo
            file_size = os.path.getsize(fpath) if os.path.exists(fpath) else 0
            
            models.append({
                'name': fname,
                'absolute_path': fpath,
                'relative_path': rel_path,
                'category': category_dir,
                'type': model_type,
                'download_url': download_url,
                'lora_purpose': lora_purpose,
                'size_bytes': file_size,
                'size_mb': round(file_size / (1024*1024), 1)
            })
    
    return models

def classify_model(fname, subdir):
    """Clasifica el tipo de modelo."""
    fn = fname.lower()
    
    if subdir == 'diffusion_models':
        if 'flux' in fn:
            return 'DIFFUSION_FLUX'
        elif 'wan' in fn:
            return 'DIFFUSION_WAN'
        elif 'ltx' in fn:
            return 'DIFFUSION_LTX'
        elif 'krea' in fn.lower():
            return 'DIFFUSION_KREA'
        else:
            return 'DIFFUSION_OTHER'
    elif subdir == 'unet':
        return 'UNET_GGUF'
    elif subdir == 'loras':
        return 'LoRA'
    elif subdir == 'vae':
        return 'VAE'
    elif subdir == 'text_encoders':
        if 'qwen' in fn:
            return 'CLIP_QWEN'
        elif 'gemma' in fn:
            return 'CLIP_GEMMA'
        elif 'umt5' in fn:
            return 'CLIP_UMT5'
        elif 'ltx' in fn:
            return 'CLIP_TEXT_PROJECTION'
        elif 'clip' in fn:
            return 'CLIP_VISION'
        else:
            return 'TEXT_ENCODER'
    elif subdir == 'clip_vision':
        return 'CLIP_VISION'
    elif subdir == 'upscale_models':
        return 'UPSCALE'
    elif subdir == 'facerestore_models':
        return 'FACERESTORE'
    elif subdir == 'foleycrafter':
        return 'FOLEYCRAFT'
    elif subdir == 'latent_upscale_models':
        return 'LATENT_UPSCALE'
    elif subdir == 'controlnet':
        return 'CONTROLNET'
    elif subdir == 'embeddings':
        return 'EMBEDDING'
    else:
        return 'UNKNOWN'

def find_download_url(fname):
    """Busca una URL de descarga conocida para el modelo."""
    # Buscar coincidencia parcial en las URLs conocidas
    fname_lower = fname.lower()
    
    for key, url in HF_MODELS.items():
        if key.lower() in fname_lower or fname_lower.replace('.safetensors', '').replace('.gguf', '').replace('.pt', '').replace('.bin', '').replace('.pth', '').replace('.ckpt', '') in key.lower():
            return url
    
    return None

def save_to_database(models):
    """Guarda los modelos en la base de datos SQLite."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Crear o actualizar tabla de modelos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            absolute_path TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            category TEXT NOT NULL,
            type TEXT NOT NULL,
            download_url TEXT,
            lora_purpose TEXT,
            size_bytes INTEGER DEFAULT 0,
            size_mb REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Crear índice para búsquedas rápidas
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_models_name ON models(name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_models_type ON models(type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_models_category ON models(category)')
    
    # Eliminar entradas antiguas y reinsertar
    cursor.execute('DELETE FROM models')
    
    for m in models:
        cursor.execute('''
            INSERT INTO models (name, absolute_path, relative_path, category, type, download_url, lora_purpose, size_bytes, size_mb)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            m['name'],
            m['absolute_path'],
            m['relative_path'],
            m['category'],
            m['type'],
            m['download_url'],
            m['lora_purpose'],
            m['size_bytes'],
            m['size_mb']
        ))
    
    conn.commit()
    conn.close()
    
    print(f"Guardados {len(models)} modelos en la BD")

def main():
    print("Escaneando modelos en /home/meisoft/ComfyUI/models...")
    models = scan_models()
    
    print(f"\nEncontrados {len(models)} modelos:")
    
    # Agrupar por tipo
    by_type = defaultdict(list)
    for m in models:
        by_type[m['type']].append(m['name'])
    
    for mtype, mnames in sorted(by_type.items()):
        print(f"\n  [{mtype}] ({len(mnames)} archivos):")
        for name in mnames[:10]:  # Limitar visualización
            print(f"    - {name}")
        if len(mnames) > 10:
            print(f"    ... y {len(mnames) - 10} más")
    
    # Mostrar LoRAs con su propósito
    lora_models = [m for m in models if m['lora_purpose']]
    if lora_models:
        print(f"\n  LoRAs detectados ({len(lora_models)}):")
        for m in lora_models:
            print(f"    - {m['name']} (propósito: {m['lora_purpose']})")
    
    # Guardar en BD
    save_to_database(models)
    
    # Guardar también como JSON para referencia rápida
    with open('/home/meisoft/ComfyUI-Hermes-Listener/models_inventory.json', 'w') as f:
        json.dump(models, f, indent=2, ensure_ascii=False)
    print(f"\nInventario guardado en models_inventory.json")

if __name__ == '__main__':
    main()
