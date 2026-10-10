// Categorías: la clasificación automática vigente, su evolución por mes y cómo ejecutar o cambiar de ejecución.
// Todo lo de esta pantalla son estimaciones; los conteos oficiales están en Resumen.
import * as api from "../api.js";
import { qs } from "../api.js";
import { categoryMiniChart } from "../charts.js";
import { h, hbars, kind, put, table } from "../dom.js";
import { errMsg, monthLabel, n, pct, utcMinute } from "../format.js";
import { PROVIDER } from "../labels.js";
import { emptyState, toast, view } from "../shell.js";
import { scope, state } from "../state.js";
export const viewCategorias = async ({ refresh }) => {
    const chatId = state.chatId;
    if (chatId == null)
        return emptyState();
    const [cats, runs, monthly, cfg] = await Promise.all([api.getCategorySummary(scope()), api.getRuns(chatId), api.getCategoryMonthly(scope()), api.getConfig()]);
    state.config = cfg;
    const taxo = cfg.taxonomy.categories;
    const jev = cfg.providers.find((p) => p.name === "jev");
    put(view, summaryCard(cats, taxo, jev), cats.run ? trendCard(monthly, taxo) : null, reviewCard(cats), runCard(chatId, runs, jev, refresh));
};
/** Cuántos mensajes tiene cada categoría según la ejecución vigente, con las correcciones humanas aplicadas. */
function summaryCard(cats, taxo, jev) {
    const run = cats.run;
    const max = Math.max(1, ...Object.values(cats.effective));
    return h("section", { class: "card" }, h("h2", null, "Categorías del período", kind("auto", "clasificación automática")), run ? h("p", null, h("small", null, `Ejecución #${run.id} · proveedor: ${run.provider === "rules" ? "reglas locales (heurística por patrones, sin IA)" : "Jev de TypeSafe"} · versión/modelo ${run.model} · taxonomía ${run.taxonomy_version} · ${utcMinute(run.started_at)} UTC. `, `${n(cats.classified_messages)} de ${n(cats.participant_messages)} mensajes de participantes tienen clasificación; ${n(cats.corrected_messages)} corregidos a mano. `, cats.note))
        : h("div", { class: "notice" }, "Aún no hay clasificación para este grupo. Ejecutá el proveedor de reglas: es local y no requiere credenciales."), run ? taxo.map((c) => h("div", null, hbars([{ label: h("a", { href: "#/explorar?" + qs({ category: c.key }) }, c.label), value: cats.effective[c.key],
            right: n(cats.effective[c.key]) + (cats.human[c.key] ? ` (${n(cats.human[c.key])} por revisión humana)` : "") }], max, n), h("small", null, c.definition))) : null, run?.provider === "jev" && jev?.score_meaning ? h("p", null, h("small", null, jev.score_meaning)) : null, run?.provider === "rules" ? h("p", null, h("small", null, "Las reglas reconocen formas frecuentes de escribir; no entienden significado. Pasá el cursor sobre una etiqueta en Explorar para ver qué regla se disparó.")) : null);
}
/** Un gráfico pequeño por categoría: qué parte de los mensajes clasificados de cada mes la tiene. */
function trendCard(monthly, taxo) {
    const trendBox = h("div");
    const trendTable = h("input", { type: "checkbox", onchange: () => drawTrend() });
    function drawTrend() {
        const ms = monthly.months.filter((x) => x.classified);
        if (!ms.length) {
            put(trendBox, h("p", { class: "muted" }, "Sin mensajes clasificados en el período."));
            return;
        }
        if (trendTable.checked) {
            put(trendBox, table([{ t: "Mes" }, { t: "Clasificados", num: true }, ...taxo.map((k) => ({ t: k.label, num: true }))], ms.map((r) => [monthLabel(r.month) + (r.partial ? " *" : ""), `${n(r.classified)} de ${n(r.participant_messages)}`,
                ...taxo.map((k) => `${n(r.counts[k.key])} (${pct(r.shares[k.key])})`)])));
            return;
        }
        put(trendBox, h("div", { class: "minis" }, taxo.map((k) => h("div", { class: "mini" }, h("h3", null, h("a", { href: "#/explorar?" + qs({ category: k.key }) }, k.label)), categoryMiniChart(ms, k.key)))));
    }
    const lowCoverage = monthly.months.filter((x) => x.classified && x.classified < x.participant_messages * 0.5);
    const trend = h("section", { class: "card" }, h("div", { class: "row spread" }, h("h2", null, "Categorías por mes", kind("auto", "clasificación automática")), h("label", { class: "row" }, trendTable, "Ver como tabla")), h("p", null, h("small", null, "Porcentaje de los mensajes clasificados de cada mes que tienen la categoría. Cada gráfico tiene su propia escala: compará la forma dentro de una categoría, no la altura entre categorías. Un mensaje puede tener varias categorías, así que los porcentajes de un mes no suman 100.")), lowCoverage.length ? h("div", { class: "notice" }, `Cobertura baja: en ${lowCoverage.map((x) => monthLabel(x.month)).join(", ")} esta ejecución clasificó menos de la mitad de los mensajes. Los porcentajes de esos meses describen solo la parte clasificada.`) : null, trendBox, h("small", null, "* Barra tenue: mes parcial. Son estimaciones automáticas (con tus correcciones aplicadas), no conteos oficiales."));
    drawTrend();
    return trend;
}
function reviewCard(cats) {
    return h("section", { class: "card" }, h("h2", null, "Revisión humana", kind("pending", "interpretación pendiente")), h("p", null, h("small", null, "Una categoría automática es una hipótesis hasta que alguien la revisa. Corregir agrega tu etiqueta sin borrar la predicción original.")), h("div", { class: "row" }, h("a", { href: "#/explorar?" + qs({ category: "oferta_de_colaboracion" }) }, "Revisar ofertas de colaboración"), "·", h("a", { href: "#/explorar?" + qs({ category: "propuesta_de_proyecto" }) }, "Revisar propuestas"), "·", h("a", { href: "#/explorar?" + qs({ human_only: 1 }) }, `Ver corregidos a mano (${n(cats.corrected_messages)})`), "·", h("a", { href: "#/explorar?" + qs({ review_only: 1, record_type: "all" }) }, "Registros marcados al importar")));
}
/** Lanzar una clasificación (reglas locales o Jev, con confirmación) y elegir cuál ejecución guardada rige. */
function runCard(chatId, runs, jev, refresh) {
    const busy = h("span", { class: "muted" });
    const start = async (provider, consent) => {
        busy.textContent = "Clasificando…";
        try {
            const r = await api.startRun({ chat_id: chatId, from: state.from, to: state.to, provider, consent });
            if (!r.activated)
                alert(`Ejecución #${r.run_id} guardada: clasificó ${n(r.classified)} de ${n(r.eligible)} mensajes.\n\nComo cubre menos que la clasificación vigente (#${r.active_run_id}), NO la reemplazó. Podés verla o activarla en "Ejecuciones guardadas", más abajo.`);
            else
                toast(`Ejecución #${r.run_id}: ${n(r.classified)} mensajes clasificados` + (r.errors ? `, ${n(r.errors)} con error` : "") + (r.truncated ? ` (tope alcanzado; había ${n(r.eligible)})` : ""));
            await refresh();
        }
        catch (e) {
            busy.textContent = "";
            alert(errMsg(e));
        }
    };
    const dataSent = jev?.data_sent ?? "";
    const jevBox = h("fieldset", null, h("legend", null, "Jev (TypeSafe) — opcional, externo"), jev?.available
        ? [h("div", { class: "notice" }, dataSent),
            h("p", null, h("small", null, `Modelo ${jev.model ?? "—"} · umbral ${jev.threshold ?? "—"} · ${jev.score_meaning ?? ""}`)),
            h("button", { class: "ghost", onclick: () => {
                    if (confirm("Vas a enviar texto de mensajes a un servicio externo (api.typesafe.ai).\n\n" + dataSent + "\n\n¿Confirmás esta ejecución?"))
                        void start("jev", true);
                } }, "Clasificar con Jev (envía texto a TypeSafe)")]
        : h("p", { class: "muted" }, `No conectado. ${jev?.reason ?? ""} Nada se envía a servicios externos.`));
    const activate = async (runId) => {
        await api.activateRun(runId);
        toast(`Ejecución #${runId} ahora es la vigente.`);
        await refresh();
    };
    return h("section", { class: "card" }, h("h2", null, "Ejecutar clasificación"), h("p", null, h("small", null, "Se clasifican los mensajes de participantes con texto del grupo y período elegidos arriba. Cada ejecución queda registrada; no reemplaza a las anteriores ni a tus correcciones.")), h("div", { class: "row" }, h("button", { onclick: () => void start("rules", false) }, "Clasificar con reglas (local)"), busy), jevBox, h("p", null, h("small", null, "OpenAI Decisions: espacio reservado en el código, no implementado.")), runs.length ? h("details", { open: runs.length > 1 }, h("summary", null, `Ejecuciones guardadas (${runs.length})`), h("p", null, h("small", null, "Ninguna ejecución borra a otra. La «vigente» es la que alimenta las categorías en toda la app; podés cambiarla cuando quieras.")), table([{ t: "#" }, { t: "Vigente" }, { t: "Proveedor" }, { t: "Modelo" }, { t: "Período" }, { t: "Estado" }, { t: "Clasificó", num: true }, { t: "Errores", num: true }, { t: "Inicio (UTC)" }], runs.map((r) => [r.id,
        r.active ? h("span", { class: "chip human" }, "vigente")
            : r.status === "completed" || r.status === "partial" ? h("button", { class: "ghost small", onclick: () => void activate(r.id) }, "Usar esta") : "—",
        PROVIDER[r.provider], r.model, `${r.date_from ?? "inicio"} → ${r.date_to ?? "fin"}`, r.status + (r.error ? ": " + r.error : ""),
        n(r.n_messages) + (r.config.eligible_messages ? " de " + n(r.config.eligible_messages) : ""), n(r.n_errors), utcMinute(r.started_at)]))) : null);
}
