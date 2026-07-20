import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

app.registerExtension({
    name: "ComfyUI.HermesListener",
    async setup() {
        // ── Refresco de estado ────────────────────────
        let statusState = null;
        async function refreshStatus() {
            try {
                const r = await api.fetchApi("/comfy_hermes/status");
                if (!r.ok) return;
                statusState = await r.json();
                _updatePanel(statusState);
            } catch (e) {
                // silencioso al inicio hasta que ComfyUI cargue
            }
        }

        // ── Descripciones de eventos ────────────────
        const EVENT_DESC = {
            execution_start:   "🟢 Inicio ejecución",
            prompt_completed:  "✅ Fin exitoso del flujo",
            execution_error:   "🔴 Error en ejecución",
            progress_update:   "📊 Progreso por paso (spammy)",
            vram_cleanup_done: "♻️ VRAM liberada tras cada flujo",
        };

        // ── Construir el panel HTML ────────────────
        const panel = buildPanel();
        function _updatePanel(s) {
            panel.setValues(s);
        }

        function buildPanel() {
            const div = document.createElement("div");
            div.innerHTML = `
                <h3>⚡ Hermes Event Listener</h3>
                <div class="hermes-status" style="font-size:12px;color:#999;min-height:18px;margin-bottom:8px;">
                    Conectando...
                </div>
                <label style="display:block;padding:4px 0;">
                    <input type="checkbox" id="hermes-enabled"/>
                    <strong>Habilitado</strong> — activar/desactivar captura global
                </label>
                <hr style="border-color:#333;margin:8px 0">
                ${Object.entries(EVENT_DESC).map(([key, label]) => `
                    <label style="display:block;padding:4px 0;">
                        <input type="checkbox" id="hermes-ev-${key}"/>
                        ${label}
                    </label>
                `).join("")}
                <hr style="border-color:#333;margin:8px 0">
                <label style="display:block;padding:4px 0;">
                    <input type="checkbox" id="hermes-vram"/>
                    <strong>Liberar VRAM</strong> tras cada ejecución (como VAE decode cleanup)
                </label>
            `;

            const setStatus = s => {
                if (!s) return div.querySelector(".hermes-status").textContent = "No disponible";
                const pid = s.last_prompt_id || "—";
                const evtDesc = s.last_event ? JSON.stringify(s.last_event).substring(0, 160) : "Ninguno aún";
                div.querySelector(".hermes-status").innerHTML =
                    `ID: <code>${pid}</code> · Último: ${evtDesc}`;
            };

            const setValues = s => {
                if (!s) return;
                document.getElementById("hermes-enabled").checked     = !!s.enabled;
                document.getElementById("hermes-vram").checked        = !!s.do_vram_cleanup;
                for (const [k, v] of Object.entries(s.events || {})) {
                    const el = document.getElementById(`hermes-ev-${k}`);
                    if (el) el.checked = !!v;
                }
                setStatus(s);
            };

            return { container: div, setValues };
        }

        // ── Recolectar valores del panel ────────────
        function collectConfig() {
            const ev = {};
            for (const key of Object.keys(EVENT_DESC)) {
                const el = document.getElementById(`hermes-ev-${key}`);
                if (el) ev[key] = el.checked;
            }
            return {
                enabled:           document.getElementById("hermes-enabled").checked,
                do_vram_cleanup:   document.getElementById("hermes-vram").checked,
                ...ev,
            };
        }

        // ── Enviar al servidor y refrescar ────────
        async function pushConfig() {
            const cfg = collectConfig();
            try {
                await api.fetchApi("/comfy_hermes/update_config", {
                    method: "POST",
                    body: JSON.stringify(cfg),
                    headers: { "Content-Type": "application/json" },
                });
            } catch (e) {
                console.error("[Hermes-Listener] Fallo guardando:", e);
            }
            await refreshStatus();
        }

        // ── Registrarse en Settings de ComfyUI ────
        app.ui.settings.addSetting({
            id: "hermes_listener.panel",
            name: "Hermes Event Listener",
            type: (name, value, settingsDom) => {
                panel.container.classList.add("comfy-settings-group");
                settingsDom.appendChild(panel.container);

                // Bindar cambios en todos los checkboxes
                panel.container.querySelectorAll("input[type=checkbox]").forEach(cb => {
                    cb.addEventListener("change", pushConfig);
                });

                return panel.container;
            },
        });

        // ── Cargar estado inicial ───────────────────
        await refreshStatus();
        setInterval(refreshStatus, 10000);
    },
});
