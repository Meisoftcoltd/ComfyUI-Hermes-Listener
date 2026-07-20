# ComfyUI-Hermes-Listener

Custom node **invisible** (zero-overhead) para ComfyUI que intercepta todos los
eventos de ejecución del motor, libera VRAM automáticamente al terminar cada
flujo y escribe señales en disco para despertar agentes externos.

100 % autónomo — no depende de n8n, webhooks remotos, ni servicios externos.

## Qué hace

| Acción | Descripción |
|---|---|
| 🟢 Captura inicio | Detecta `execution_start` al enviar un prompt a la cola |
| ✅ Captura fin | Detecta `executing` con `node=None` → flujo completado sin errores |
| 🔴 Captura error | Detecta `execution_error` con nodo fallido, tipo de excepción y traceback |
| ♻️ VRAM cleanup | Libera memoria GPU automaticamente tras cada flujo (como SequentialBatcher) |
| 📁 Escritura señal | Guarda `signal_hermes.json` con el último evento para lectura local |

## Instalación

```bash
cd ~/ComfyUI/custom_nodes
git clone https://github.com/Meisoftcoltd/ComfyUI-Hermes-Listener.git
# Reiniciar ComfyUI
```

También disponible via **ComfyUI Manager** → Install Custom Nodes → buscar
*Hermes Listener*.

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

## Flujo con Agente Externo

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
