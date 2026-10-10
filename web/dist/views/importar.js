// Importar: analizar una exportación, elegir cómo leer la fecha y a qué grupo va, y administrar los grupos ya cargados.
// El archivo se analiza primero (vista previa) y solo se guarda cuando la persona confirma.
import * as api from "../api.js";
import { ApiError } from "../api.js";
import { h, kind, put, table } from "../dom.js";
import { errMsg, n, utcMinute } from "../format.js";
import { toast, syncScope, view } from "../shell.js";
import { config, loadChats, selectChat, state } from "../state.js";
const ISSUE_COLUMNS = [{ t: "Líneas" }, { t: "Motivo" }, { t: "Extracto" }];
const issueRows = (issues) => issues.map((i) => [`${i.line_start}–${i.line_end}`, i.detail, i.excerpt.slice(0, 160)]);
export const viewImportar = async () => {
    const out = h("div");
    const file = h("input", { type: "file", accept: ".txt,.zip,text/plain,application/zip" });
    const tz = h("input", { value: config().default_timezone, list: "tzlist", size: 24 });
    const zones = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : ["America/Costa_Rica", "UTC"];
    const tzlist = h("datalist", { id: "tzlist" }, zones.map((z) => h("option", { value: z })));
    let buf = null;
    const admin = h("div");
    async function preview(dateOrder) {
        const picked = file.files?.[0];
        if (!picked)
            return;
        const data = buf ?? await picked.arrayBuffer();
        buf = data;
        const name = picked.name;
        put(out, h("p", { class: "muted" }, "Analizando…"));
        let p;
        try {
            p = await api.previewImport(data, name, tz.value, dateOrder);
        }
        catch (e) {
            put(out, h("p", { class: "error" }, errMsg(e)));
            return;
        }
        const dateBox = dateOrderBox(p, (order) => void preview(order));
        const d = p.diagnostics, rec = p.recommendation, order = p.date_order;
        // Sin orden de fecha no hay diagnóstico ni destino: primero hay que elegirlo.
        if (p.needs_date_order || !d || !rec || !order) {
            put(out, dateBox);
            return;
        }
        put(out, h("h3", null, "Diagnóstico de importación", kind("fact", "hecho")), diagRows(d), d.warnings.map((w) => h("div", { class: "notice" }, w)), d.issues.length ? h("details", null, h("summary", null, `Líneas no convertidas en registro (${d.issues.length})`), table(ISSUE_COLUMNS, issueRows(d.issues))) : null, dateBox, destinationForm(p, rec, order, { name, timezone: () => tz.value }, renderChatsAdmin));
    }
    file.addEventListener("change", () => { buf = null; void preview(""); });
    async function renderChatsAdmin() {
        await loadChats();
        const blocks = await Promise.all(state.chats.map(async (c) => chatCard(c, await api.getChatImports(c.id), renderChatsAdmin)));
        put(admin, h("h2", null, "Grupos importados"), blocks.length ? blocks : h("p", { class: "muted" }, "Todavía no hay grupos."));
    }
    put(view, h("section", { class: "card" }, h("h2", null, "Importar exportación de WhatsApp (.zip o .txt)"), h("p", null, h("small", null, "El archivo se procesa en esta máquina y se guarda en una base SQLite local. El original no se modifica. Reimportar el mismo archivo no duplica nada.")), h("div", { class: "row end" }, h("label", { class: "field" }, "Archivo TXT", file), h("label", { class: "field" }, "Zona horaria de las horas del archivo", tz), tzlist), out), h("section", null, admin));
    await renderChatsAdmin();
};
function diagRows(d) {
    const row = (label, value) => [h("span", null, label), h("span", null, value)];
    return h("div", { class: "kv" }, row("Líneas del archivo", n(d.lines_total)), row("Registros detectados", n(d.records_detected)), row("Mensajes de participantes", n(d.participant_messages)), row("Avisos del sistema", n(d.system_notices)), row("No reconocidos (para revisión)", n(d.unrecognized)), row("Con fecha no interpretable", n(d.invalid_date_records)), row("Bloques sin fecha al inicio", n(d.preamble_blocks)), row("Marcados para revisión", n(d.flagged_for_review)), row("Registros multilínea", n(d.multiline_records)), row("Multimedia omitida", n(d.media_omitted_messages)), row("Identificadores de autor", n(d.distinct_author_labels)), row("Período disponible", d.period_start && d.period_end ? `${d.period_start.slice(0, 16)} → ${d.period_end.slice(0, 16)}` : "—"), row("Formato", `${d.format_variant === "bracket" ? "[fecha] autor:" : "fecha - autor:"} · reloj ${d.clock ?? "?"}${d.has_seconds ? " con segundos" : ""}`));
}
const DATE_ORDERS = [["", "Elegí…"], ["MDY", "mes/día/año"], ["DMY", "día/mes/año"], ["YMD", "año/mes/día"]];
const isDateOrder = (v) => v === "MDY" || v === "DMY" || v === "YMD";
function dateOrderBox(p, onPick) {
    const det = p.date_detection, ev = det.evidence;
    const orderSel = h("select", null, DATE_ORDERS.map(([v, t]) => h("option", { value: v, selected: p.date_order === v }, t)));
    orderSel.addEventListener("change", () => { if (isDateOrder(orderSel.value))
        onPick(orderSel.value); });
    let explanation;
    if (det.status === "detected" && det.order) {
        const dayFirst = det.order === "DMY";
        explanation = h("p", null, `Detectado: ${det.order}. `, h("small", null, det.order === "YMD" ? ev.reason ?? "" :
            `Evidencia: ${n(dayFirst ? ev.first_field_gt12 : ev.second_field_gt12)} registros tienen un valor mayor que 12 en el ${dayFirst ? "primer" : "segundo"} campo (p. ej. línea ${(dayFirst ? ev.example_line_first : ev.example_line_second) ?? "?"}), así que ese campo es el día.`));
    }
    else if (det.status === "ambiguous") {
        explanation = h("div", { class: "notice" }, `El archivo no permite distinguir día/mes: ningún valor supera 12 (ejemplo: ${ev.example_raw ?? "?"}). `, ev.hint ? `Pista no concluyente: solo ${ev.hint} mantiene las fechas en orden cronológico. ` : "", "Elegí el orden para continuar; no se asume ninguno.");
    }
    else {
        explanation = h("div", { class: "notice" }, "Evidencia contradictoria: hay registros con valores mayores que 12 en ambos campos (¿archivo mezclado?). Elegí un orden; los registros que no encajen quedarán listados como fecha inválida.");
    }
    return h("fieldset", null, h("legend", null, "Orden de la fecha"), explanation, p.date_order_warning ? h("div", { class: "notice" }, p.date_order_warning) : null, h("label", { class: "field" }, "Interpretar como", orderSel));
}
/** Paso final de la vista previa: a qué grupo va el archivo y el botón que lo guarda. */
function destinationForm(p, rec, order, file, onImported) {
    const { name } = file;
    const dest = h("select", null, h("option", { value: "" }, "Crear grupo nuevo"), p.candidates.map((c) => h("option", { value: c.chat_id, selected: rec.chat_id === c.chat_id }, `Actualizar «${c.name}»` + (c.ratio != null ? ` — coinciden ${n(c.matched)} de ${n(c.new_in_window)} del tramo compartido` : " — sin tramo compartido"))));
    const newName = h("input", { value: p.suggested_name, size: 32 });
    const nameField = h("label", { class: "field" }, "Nombre del grupo nuevo", newName);
    const syncName = () => { nameField.hidden = dest.value !== ""; };
    dest.addEventListener("change", syncName);
    syncName();
    const go = h("button", { disabled: rec.action === "duplicate" }, "Importar");
    const msg = h("div");
    const send = (confirmLow) => api.commitImport({
        token: p.token, file_name: name, chat_id: dest.value ? Number(dest.value) : null, chat_name: newName.value,
        timezone: file.timezone(), date_order: order, confirm_low_overlap: confirmLow,
    });
    go.addEventListener("click", () => void (async () => {
        go.disabled = true;
        try {
            let r;
            try {
                r = await send(false);
            }
            catch (e) {
                // Baja coincidencia con el chat elegido: solo se continúa con confirmación explícita.
                if (e instanceof ApiError && e.body?.error === "low_overlap" && confirm(e.message))
                    r = await send(true);
                else
                    throw e;
            }
            await loadChats();
            selectChat(r.chat_id);
            syncScope();
            const text = r.status === "duplicate" ? r.message
                : `Importado. ${n(r.reconciliation.added)} registros nuevos; ${n(r.reconciliation.matched_existing)} ya estaban (solo se anotó la nueva fuente); ${n(r.reconciliation.only_in_previous)} existen solo en importaciones previas y se conservaron; ${n(r.reconciliation.ambiguous_pairs)} quedaron marcados como solape ambiguo. `;
            put(msg, h("div", { class: "notice" }, text, " ", h("a", { href: "#/resumen" }, "Ver resumen →")));
            await onImported();
        }
        catch (e) {
            put(msg, h("p", { class: "error" }, errMsg(e)));
            go.disabled = false;
        }
    })());
    return [
        h("fieldset", null, h("legend", null, "Destino"), h("p", null, h("small", null, rec.action === "update" ? "Sugerencia: actualización de un grupo existente. " : rec.action === "duplicate" ? "Archivo ya importado. " : "Sugerencia: grupo nuevo. ", rec.reason)), h("div", { class: "row end" }, h("label", { class: "field" }, "Importar en", dest), nameField), h("p", null, h("small", null, "Al actualizar, los mensajes se alinean por secuencia y contexto: no se borran mensajes existentes y las repeticiones legítimas se conservan."))),
        h("div", { class: "row" }, go), msg
    ];
}
/** Un grupo ya importado: sus cifras, su zona horaria, sus importaciones y el botón para eliminarlo. */
function chatCard(c, imps, reload) {
    const tzIn = h("input", { value: c.timezone, list: "tzlist", size: 22 });
    const remove = async () => {
        if (!confirm(`¿Eliminar «${c.name}» y todos sus datos derivados (mensajes, índice de búsqueda, clasificaciones, correcciones, ediciones del periódico y el texto de las páginas leídas para ellas)?\n\nNo se puede deshacer. Tus archivos TXT originales no se tocan.`))
            return;
        const r = await api.deleteChat(c.id);
        toast(`Eliminado: ${n(r.records_removed)} registros.`);
        if (state.chatId === c.id)
            selectChat(null);
        await loadChats();
        syncScope();
        await reload();
    };
    const saveZone = async () => {
        try {
            await api.updateChatTimezone(c.id, tzIn.value);
            toast("Zona actualizada; fechas recalculadas.");
            await reload();
        }
        catch (e) {
            alert(errMsg(e));
        }
    };
    return h("div", { class: "card" }, h("div", { class: "row spread" }, h("h3", null, c.name), h("button", { class: "danger small", onclick: () => void remove() }, "Eliminar grupo y datos")), h("p", null, h("small", null, `${n(c.participant_messages)} mensajes de participantes · ${c.first_record?.slice(0, 10) ?? "—"} → ${c.last_record?.slice(0, 10) ?? "—"}`)), h("div", { class: "row end" }, h("label", { class: "field" }, "Zona horaria del chat", tzIn), h("button", { class: "ghost small", onclick: () => void saveZone() }, "Guardar zona")), h("small", null, "El TXT no indica zona horaria: las horas son las del teléfono que exportó. Esta zona declara cómo interpretarlas."), imps.map((i) => h("details", null, h("summary", null, `Importación #${i.id}: ${i.file_name} · ${utcMinute(i.imported_at)} UTC`), h("p", null, h("small", null, `Fecha ${i.date_order} (${i.date_order_source === "detected" ? "detectada" : "elegida por vos"}) · zona ${i.timezone} · SHA-256 ${i.file_sha256.slice(0, 12)}… · ` +
        `reconciliación: ${n(i.reconciliation.added)} nuevos, ${n(i.reconciliation.matched_existing)} ya existentes, ${n(i.reconciliation.ambiguous_pairs)} ambiguos (precisión: ${i.reconciliation.precision}).`)), diagRows(i.diagnostics), i.issues.length ? table(ISSUE_COLUMNS, issueRows(i.issues)) : null)));
}
