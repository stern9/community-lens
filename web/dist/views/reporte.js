// Reporte: texto agregado del grupo y período, para compartir fuera de la app.
import * as api from "../api.js";
import { h, put } from "../dom.js";
import { emptyState, toast, view } from "../shell.js";
import { scope, state } from "../state.js";
export const viewReporte = async () => {
    if (state.chatId == null)
        return emptyState();
    const inc = h("input", { type: "checkbox" });
    const pre = h("pre", { class: "report" });
    const refresh = async () => { pre.textContent = await api.getReportText(scope(), inc.checked); };
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(pre.textContent ?? "");
            toast("Texto copiado.");
        }
        catch {
            toast("No se pudo copiar; seleccioná el texto.");
        }
    };
    inc.addEventListener("change", () => void refresh());
    put(view, h("section", { class: "card" }, h("h2", null, "Reporte agregado para compartir"), h("p", null, h("small", null, "Contiene solo datos agregados del grupo y período elegidos arriba. No incluye mensajes ni citas, y no se publica en ningún lado: vos decidís a quién enviarlo.")), h("label", { class: "row" }, inc, "Incluir los 10 identificadores con más mensajes (nombres o teléfonos: datos personales)"), h("div", { class: "actions" }, h("button", { onclick: () => { window.open(api.reportUrl(scope(), "html", inc.checked), "_blank", "noopener"); } }, "Abrir HTML"), h("button", { class: "ghost", onclick: () => { location.href = api.reportUrl(scope(), "html", inc.checked, true); } }, "Descargar HTML"), h("button", { class: "ghost", onclick: () => { location.href = api.reportUrl(scope(), "txt", inc.checked, true); } }, "Descargar TXT"), h("button", { class: "ghost", onclick: () => void copy() }, "Copiar texto")), pre));
    await refresh();
};
