---
name: comfyui-hermes-listener
display_name: ComfyUI-Hermes-Listener Integration Guide for Hermes Agent
category: devops
version: 1.0.0
description: |
  Skill para integrar ComfyUI-Hermes-Listener y ejecutar workflows con flujo ordenado:
  dispatch → zero-token idle → despertar por señal local → depurar error o continuar.
---

# ComfyUI-Hermes-Listener — Guía de integración Hermes

## Cómo funciona el flujo

1. **Enviando workflow:** Agente POST `/prompt` vía curl o API de comfyui-mcp
2. **Agente queda en reposo:** Cero tokens mientras GPU procesa
3. **ComfyUI ejecuta workflow** con GPU a plena carga
4. **Listener captura evento:**
   - `execution_start` → escribe `inicio` en signal file
   - `prompt_completed` → escribe `fin` + VRAM stats
   - `execution_error` → escribe `error` + detalle del fallo
5. **Hermes detecta señal** (polling o watch) y despierta
6. **Agente evalúa resultado:**
   - ✅ Éxito: leer signal file con info de VRAM, continuar flujo siguiente paso
   - ❌ Error: depurar (nodo fallido + motivo), corregir workflow, reintentar

## Verificar estado del listener

```bash
curl -s http://127.0.0.1:8189/comfy_hermes/status | python3 -m json.tool
```

Retorna: `{enabled, events_config, do_vram_cleanup, last_prompt_id, last_event}`

## Liberar VRAM manualmente

```bash
curl -X POST http://127.0.0.1:8189/comfy_hermes/free_vram
```

Retorna stats de memoria antes/despues liberacion.

## Actualizar configuracion del listener desde terminal

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"enabled": true, "execution_start": true, "prompt_completed": true,' \
     '"do_vram_cleanup": true, "progress_update": false}' \
  http://127.0.0.1:8189/comfy_hermes/update_config
```

## Leer archivo de señal

```bash
cat /home/meisoft/ComfyUI/custom_nodes/ComfyUI-Hermes-Listener/signal_hermes.json
# o leer via python para JSON parseado
python3 -c "import json; print(json.load(open('/home/meisoft/ComfyUI/custom_nodes/ComfyUI-Hermes-Listener/signal_hermes.json')))"
```

## Flujo tipico de uso en conversacion

**Paso 1:** Agente envia prompt a ComfyUI para generar imagen/video
```bash
curl -X POST http://127.0.0.1:8189/prompt --header "Content-Type: application/json" \
  -d '{"prompt": {...tu workflow JSON...}}'
```

**Paso 2:** Agente queda en estado reposo. No hace polling ni consume tokens.

**Paso 3:** Usuario o proceso externo monitorea el archivo de señal:
```bash
while true; do inotifywait -q -e modify signal_hermes.json && break; sleep 1; done
```

**Paso 4:** Cuando se dispara, leer contenido y decidir accion siguiente basandose en
el campo `estado` (inicio/fin/error).

## Notas importantes

- VRAM cleanup AUTOMATICO si `do_vram_cleanup=true`. No necesitas activarlo manualmente a menos que quieras control manual.
- Los eventos pueden filtrarse completamente desactivados desde panel UI de Config o API REST update_config.
- El archivo signal se actualiza atomicamente (write + os.replace) para evitar lecturas corruptas.

## Troubleshooting

### Signal file no existe / vacio
```bash
ls -l /home/meisoft/ComfyUI/custom_nodes/ComfyUI-Hermes-Listener/signal_hermes.json*
# Verificar que ComfyUI corriendo con listener activo:
curl -s http://127.0.0.1:8189/comfy_hermes/status | grep enabled
```

### VRAM no se libera / sigue en alto
Verificar `do_vram_cleanup` esta true y hacer limpieza manual:
```bash
curl -X POST http://127.0.0.1:8189/comfy_hermes/free_vram | python3 -m json.tool
```

### ComfyUI no reconoce el custom node
Reiniciar ComfyUI con `comfy` (alias) o ver logs de carga para mensajes de error del modulo.
