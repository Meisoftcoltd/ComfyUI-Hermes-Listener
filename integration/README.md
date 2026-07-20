# Hermes Integration Pack

Este directorio contiene todo lo necesario para que **Hermes Agent** trabaje con `ComfyUI-Hermes-Listener` en un flujo ordenado: dispatch → idle → despertar por señal → continuar o depurar.

## Archivos incluidos

| Archivo | Proposito |
|---|---|
| `comfyui_hermes_mcp.toml` | Definicion de tool MCP para interactuar con la API REST del listener |
| `SKILL.md` | Guia completa de integracion + comandos esenciales para Hermes Agent |

## Instalacion en Hermes

### 1. Registrar herramienta MCP (opcional)

Copiar el archivo TOML al directorio de herramientas de Hermes:

```bash
cp comfyui_hermes_mcp.toml ~/.hermes/mcp/comfyui_hermes_signal/
```

O usarlo directamente desde la ruta original si tu config lo permite.

### 2. Registrar Skill

Ejecutar en Hermes:
```
skill_manage(action='create', name='comfyui-hermes-listener', content=<contenido de SKILL.md>)
```

O copiar al directorio manual:
```bash
cp SKILL.md ~/.hermes/skills/comfyui-hermes-listener.md
```

### 3. Prompt de arranque para Hermes

Usar este prompt en tu sesion con Hermes para que entienda el flujo completo:

---

**Prompt:**
"""
Actua como el agente conductor del pipeline ComfyUI+GPU+Hermes.

FLUJO ORDENADO OBLIGATORIO:
1. Enviar workflow a http://127.0.0.1:8189/prompt via curl POST JSON
2. Tras recibir prompt_id, queda en REPOSO TOTAL (cero tokens, cero polling)
3. Monitorea el archivo /home/meisoft/ComfyUI/custom_nodes/ComfyUI-Hermes-Listener/signal_hermes.json:
   - estado "inicio"   -> confirma ejecucion comenzada, sigue esperando
   - estado "fin"     -> exito! Lee VRAM stats, continua con siguiente paso del flujo
   - estado "error"   -> deteccion de fallo. Carga detalles (nodo, motivo), analiza error,
                         corrige workflow JSON si necesario, reenvia a /prompt y vuelve a reposo
   - estado "vram_freed" -> VRAM libre para siguiente task

VERIFICACION POST-PROCESO:
Si despues de 300 segundos no hay señal, hacer health check:
curl http://127.0.0.1:8189/comfy_hermes/status

IMPORTANTE: Nunca uses polling activo durante los primeros 60 seg despues de enviar prompt.
La GPU necesita trabajar tranquila. Solo consulta la senal cuando haya transcurrido tiempo razonable o el usuario lo solicite.
"""

---

## Verificacion de estado

```bash
# Comprobar que el listener esta activo y configurado correctamente
curl -s http://127.0.0.1:8189/comfy_hermes/status | python3 -m json.tool

# Probar limpieza VRAM desde terminal
curl -X POST http://127.0.0.1:8189/comfy_hermes/free_vram

# Ver archivo de senal actual
cat /home/meisoft/ComfyUI/custom_nodes/ComfyUI-Hermes-Listener/signal_hermes.json 2>/dev/null || echo "Sin sena aun"
```
