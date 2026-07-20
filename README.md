# ComfyUI-Hermes-Listener

Custom node **invisible** (zero-overhead) para ComfyUI que intercepta todos los
eventos de ejecución del motor, libera VRAM automáticamente al terminar cada
flujo y escribe señales en disco para despertar agentes externos.

Diseñado para funcionar **100 % en local** — sin n8n, sin webhooks remotos,
sin servicios de terceros. Solo ComfyUI + Hermes (u otro agente) + Ollama
(o modelo LLM local equivalente).

## Qué hace

| Acción | Descripción |
|---|---|
| 🟢 Captura inicio | Detecta `execution_start` al enviar un prompt a la cola |
| ✅ Captura fin | Detecta `executing` con `node=None` → flujo completado sin errores |
| 🔴 Captura error | Detecta `execution_error` con nodo fallido, tipo de excepción y traceback |
| ♻️ VRAM cleanup | Libera memoria GPU automaticamente tras cada flujo (como SequentialBatcher) |
| 📁 Escritura señal | Guarda `signal_hermes.json` con el último evento para lectura local |

## Instalación

### Requisitos previos

Este proyecto está diseñado para funcionar **100 % en local** con el siguiente stack:

- **ComfyUI** → motor de generación de imágenes
- **Hermes Agent** → agente de IA que envía workflows y recibe señales (también válido con cualquier agente similar)
- **Ollama** → modelo LLM local para procesamiento de texto (o cualquier LLM local equivalente)

No requiere n8n, webhooks remotos, ni servicios de terceros. Todo ocurre en tu máquina.

### Instalación automática (desde agente Hermes)

El agente puede instalar automáticamente este listener:

1. **Clonar en custom_nodes de ComfyUI:**
   ```bash
   cd ~/ComfyUI/custom_nodes
   git clone https://github.com/Meisoftcoltd/ComfyUI-Hermes-Listener.git
   ```

2. **Reiniciar ComfyUI** para que cargue el listener automáticamente.

3. **Verificar instalación:**
   - En la UI de ComfyUI (⚙️ Settings) debe aparecer la sección *"Hermes Event Listener"*
   - O consultar la API: `GET http://127.0.0.1:8189/comfy_hermes/status`

### Instalación manual

```bash
cd ~/ComfyUI/custom_nodes
git clone https://github.com/Meisoftcoltd/ComfyUI-Hermes-Listener.git
# Reiniciar ComfyUI
```

También disponible via **ComfyUI Manager** → Install Custom Nodes → buscar
*Hermes Listener*.

## Base del proyecto

Este listener se basa en el diseño de **[artokun/comfyui-mcp](https://github.com/artokun/comfyui-mcp)** (el MCP server para ComfyUI que expone 108+ herramientas para ejecutar workflows, gestionar modelos, controlar VRAM y explorar nodos desde asistentes de IA).

Adaptamos su arquitectura para integrarlo como nodo invisible (zero-overhead) dentro de ComfyUI, capturando eventos nativos de ejecución y escribiendo señales locales para despertar agentes externos como Hermes.

> **Nota:** El MCP se integra directamente con ComfyUI y el agente local (Hermes/Ollama), sin necesidad de n8n ni servicios externos.

## Configuración en UI

1. Abrir el panel de ajuste de ComfyUI (botón ⚙️ Settings).
2. Buscar sección **"Hermes Event Listener"**.
3. Toggles individuales: habilitar/deshabilitar eventos, activar/desactivar
   limpieza VRAM automática.
4. Los cambios se guardan automáticamente cada vez que modificas un checkbox.

### Eventos configurables

- **execution_start** — notifica cuando comienza la ejecución
- **prompt_completed** — notifica cuando el prompt termina con éxito
- **execution_error** — notifica fallos incluyendo nodo y motivo
- **progress_update** — progreso incremental (desactivado por defecto, es spammy)
- **vram_cleanup_done** — confirma que VRAM fue liberada

## API REST interna

```http
GET  /comfy_hermes/status          # Estado actual del listener + último evento
POST /comfy_hermes/update_config   { "enabled": true, "execution_error": true, ... }
POST /comfy_hermes/free_vram       # Liberar VRAM manualmente
```

## Archivo de señal

Cada vez que ocurre un evento capturado (inicio/fin/error), el nodo escribe:

```json
{
  "estado": "fin",
  "timestamp": "2025-07-21T10:35:12+00:00",
  "prompt_id": "abc-def-ghi",
  "vram_before_gb": 18.4,
  "vram_after_gb": 1.2,
  "vram_freed_gb": 17.2,
  "comfy_event": "executing"
}
```

Ubicación: `ComfyUI-Hermes-Listener/signal_hermes.json` (junto a `config.json`).
El archivo se actualiza de forma atómica (`write + os.replace`) para evitar
lecturas corruptas.

## Aprendizaje automático de nodos

El módulo `nodes/comfyui_nodes.py` permite que el agente aprenda automáticamente de workflows:

1. **Escaneo inicial:** `init_db()` descarga `/object_info` de ComfyUI y escanea el filesystem de modelos
2. **Análisis:** `analyze_workflow("id", workflow_json)` detecta nodos faltantes y conexiones válidas
3. **Consulta:** `get_compatible_nodes("LATENT")` encuentra nodos que producen un tipo de dato
4. **Memoria:** Cada workflow analiza, cada conexión válida se guarda, cada nodo se contabiliza

```python
from nodes.comfyui_nodes import init_db, analyze_workflow, get_db_stats

# Escaneo inicial (ejecutar una sola vez al instalar)
init_db()

# Analizar un workflow antes de ejecutar
result = analyze_workflow("wf_001", workflow_json)
print(result)
# {
#   "status": "missing_nodes",
#   "missing_nodes": ["NodoDesconocido"],
#   "resolved_connections": 15,
#   "total_nodes": 18
# }

# Ver estadísticas de la DB
stats = get_db_stats()
# { "nodes_registered": 340, "models_scanned": 42, "connections_registered": 120, ... }
```

Cada vez que el agente analiza un workflow:
- **Nodos faltantes** → registrados en `workflow_analysis` para que el agente los añada manualmente
- **Conexiones válidas** → guardadas en `node_connections` para consultas futuras
- **Uso de nodos** → contabilizado en `node_usage_stats` para identificar los más usados

## Flujo completo con agente Hermes

```
Agente → POST /prompt → [prompt_id]
  |
  ↓  Agente entra en REPOSO (cero tokens)
  |
ComfyUI ejecuta workflow → GPU processing...
  |
  ↓  Listener captura evento
  |
signal_hermes.json actualizado
  |
  ↓  Hermes monitorea el archivo / lee vía API
  |
Agente despierta:
  ├── estado "fin"   → éxito → continúa flujo o usa workflow nuevo
  └── estado "error" → depura, corrige workflow, reintenta
```

## Licencia

MIT — David Martín / Meisoftcoltd
