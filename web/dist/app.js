// Community Lens — vistas y enrutamiento.
// Se compila con `npm run build` a web/dist (versionado, para que la app corra sin Node).
import * as api from "./api.js";
import { ApiError, qs } from "./api.js";
import { calendarChart, categoryMiniChart, cellKey, cellMap, hourHeatmap, monthChart, simpleBars } from "./charts.js";
import { byId, h, hbars, kind, put, table } from "./dom.js";
import { DAY_ORDER, dec, errMsg, monthLabel, n, pad2, pct, weekdayName } from "./format.js";
const state = { chats: [], chatId: null, from: "", to: "", config: null, authors: [] };
const view = byId("view", HTMLElement);
const drawer = byId("drawer", HTMLElement);
const scope = () => ({ chat_id: state.chatId, from: state.from, to: state.to });
function config() {
    if (!state.config)
        throw new Error("La configuración todavía no se cargó");
    return state.config;
}
const categories = () => config().taxonomy.categories;
const catLabel = (k) => categories().find((c) => c.key === k)?.label ?? k;
const PROVIDER = { rules: "reglas", jev: "Jev", openai_decisions: "OpenAI Decisions" };
const SUBTYPE = {
    join: "entradas", leave: "salidas", security_code: "códigos de seguridad", group_change: "cambios del grupo",
    e2e_notice: "aviso de cifrado", generic_notice: "aviso genérico", other: "otros",
};
const REVIEW = {
    aviso_no_reconocido: "aviso no reconocido", marcador_desconocido: "marcador desconocido del exportador",
    solape_ambiguo: "solape ambiguo entre exportaciones",
};
const ISSUE_COLUMNS = [{ t: "Líneas" }, { t: "Motivo" }, { t: "Extracto" }];
const issueRows = (issues) => issues.map((i) => [`${i.line_start}–${i.line_end}`, i.detail, i.excerpt.slice(0, 160)]);
let toastTimer;
function toast(msg) {
    const t = byId("toast", HTMLElement);
    t.textContent = msg;
    t.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { t.hidden = true; }, 3200);
}
const utcMinute = (iso) => iso.replace("T", " ").slice(0, 16);
// ------------------------------------------------------------------ resumen --
async function viewResumen(params) {
    if (state.chatId == null)
        return emptyState();
    const [s, au, cats] = await Promise.all([api.getStats(scope()), api.getAuthors(scope()), api.getCategorySummary(scope())]);
    state.authors = au.authors;
    const t = s.totals, c = s.concentration, m = t.participant_messages, av = s.averages;
    const of = (x) => `${pct(m ? x / m : null)} de ${n(m)} mensajes de participantes`;
    const metric = (label, value, note) => h("div", { class: "metric" }, h("div", { class: "l" }, label), h("div", { class: "v" }, value), h("div", { class: "n" }, note));
    const partial = s.monthly.filter((x) => x.partial);
    const silentDays = s.conversation.silent_days;
    const weekendSilent = silentDays.filter((d) => d.weekday === 0 || d.weekday === 6).length;
    const defs = s.definitions;
    const definitionList = [
        ["Mensajes de participantes", defs.participant_messages], ["Avisos del sistema", defs.system_notices],
        ["Autores activos", defs.active_authors], ["Días activos", defs.active_days], ["Promedios", defs.averages],
        ["Días de conversación", defs.conversation_days], ["Días sin mensajes", defs.silent_days],
        ["Los N más activos", defs.concentration], ["Mensajes con enlaces", defs.messages_with_links],
        ["Enlaces en total", defs.total_links], ["Dominios", defs.domains], ["Multimedia omitida", defs.media_omitted],
        ["Eliminados", defs.deleted], ["Mes parcial", defs.partial_month], ["Miembros silenciosos", defs.silent_members],
    ];
    const facts = h("section", { class: "card" }, h("h2", null, "Resumen del período", kind("fact", "hechos calculados")), h("p", { class: "muted" }, s.period.first_day
        ? `${s.period.first_day} → ${s.period.last_day ?? ""} · ${n(t.calendar_days)} días calendario · zona ${s.chat.timezone}. Historial importado: ${s.coverage.first_record?.slice(0, 10) ?? "—"} → ${s.coverage.last_record?.slice(0, 10) ?? "—"}.`
        : "No hay registros en el período elegido."), h("div", { class: "grid" }, metric("Mensajes de participantes", n(m), `de ${n(t.records)} registros; ${n(t.system_notices)} avisos del sistema no cuentan`), metric("Identificadores de autor activos", n(t.active_authors), "nombres o teléfonos tal como aparecen; no son «miembros»"), metric("Días activos", n(t.active_days), `de ${n(t.conversation_days)} días desde el primer mensaje · ${n(t.silent_days)} sin mensajes`), metric("Promedio de mensajes por día", dec(av.per_day), `${n(m)} mensajes en ${n(av.per_day_denominator)} días de conversación`), metric("Promedio de mensajes por mes", dec(av.per_month), av.per_month_denominator
        ? `${n(av.per_month_messages)} mensajes en ${av.per_month_denominator} meses completos; sin los parciales`
        : "no hay meses completos en el período"), metric("Promedio de mensajes por hora", dec(av.per_hour, 2), av.peak_hour != null
        ? `contando las 24 horas · hora pico ${pad2(av.peak_hour)}:00, con ${dec(av.peak_hour_per_day)} por día`
        : "sin mensajes"), metric("Mensajes con enlaces", n(t.messages_with_links), of(t.messages_with_links)), metric("Enlaces en total", n(t.total_links), `en ${n(t.messages_with_links)} mensajes · ${n(t.distinct_domains)} dominios`), metric("Multimedia omitida", n(t.media_omitted_messages), of(t.media_omitted_messages)), metric(`Los ${c.top5.n} participantes más activos`, pct(c.top5.share), `escribieron ${n(c.top5.messages)} de ${n(m)} mensajes`), metric(`Los ${c.top10.n} participantes más activos`, pct(c.top10.share), `escribieron ${n(c.top10.messages)} de ${n(m)} mensajes`)), h("p", null, h("small", null, "«Más activos» se mide por cantidad de mensajes, no por la calidad ni el valor de los aportes. ", `Además: ${n(t.deleted_messages)} mensajes eliminados, ${n(t.edited_messages)} editados, ${n(t.polls)} encuestas.`)), partial.length ? h("div", { class: "notice" }, "Meses parciales: ", partial.map((x) => `${monthLabel(x.month)} (${x.days_covered} de ${x.days_in_month} días)`).join(", "), ". No son comparables con meses completos. El último día del historial termina al momento de exportar.") : null, silentDays.length ? h("details", null, h("summary", null, `Días sin mensajes (${n(silentDays.length)} de ${n(t.conversation_days)})`), h("p", null, silentDays.map((d) => `${weekdayName(d.weekday).toLowerCase()} ${d.day}`).join(" · ")), h("small", null, `${n(weekendSilent)} de ${n(silentDays.length)} cayeron en sábado o domingo. Se cuenta desde el primer mensaje de un participante (${s.conversation.first_day ?? "—"}), no desde la creación del grupo; en esos días puede haber avisos del sistema.`)) : null, h("p", null, h("small", null, "¿De dónde sale cada cifra? ", h("a", { href: "/como-funciona", target: "_blank", rel: "noopener" }, "Ver el flujo de datos de la app ↗"))), h("details", null, h("summary", null, "Definiciones y denominadores"), h("dl", { class: "defs" }, definitionList.map(([term, text]) => [h("dt", null, term), h("dd", null, text)]))));
    // Actividad en el tiempo
    const chartBox = h("div");
    const mode = h("select", { "aria-label": "Vista", onchange: () => drawActivity() }, h("option", { value: "month" }, "Por mes"), h("option", { value: "day", selected: params.get("vista") === "dia" }, "Calendario diario"));
    const asTable = h("input", { type: "checkbox", onchange: () => drawActivity() });
    function drawActivity() {
        const byMonth = mode.value === "month";
        if (!(byMonth ? s.monthly : s.daily).length) {
            put(chartBox, h("p", { class: "muted" }, "Sin datos."));
            return;
        }
        if (asTable.checked) {
            put(chartBox, byMonth
                ? table([{ t: "Mes" }, { t: "Mensajes", num: true }, { t: "Identificadores activos", num: true }, { t: "Días activos", num: true }, { t: "Días cubiertos", num: true }], s.monthly.map((r) => [monthLabel(r.month) + (r.partial ? " *" : ""), n(r.messages), n(r.authors), n(r.active_days), `${r.days_covered} de ${r.days_in_month}`]))
                : table([{ t: "Día" }, { t: "Mensajes", num: true }, { t: "Identificadores activos", num: true }], s.daily.map((r) => [r.day, n(r.messages), n(r.authors)])));
            return;
        }
        put(chartBox, byMonth ? monthChart(s.monthly) : calendarChart(s.daily), byMonth && partial.length ? h("small", null, "* Barra tenue: mes parcial, no comparable con meses completos.") : null, byMonth ? null : h("small", null, "Cada celda es un día; color más intenso = más mensajes. Los días sin mensajes quedan en el tono más tenue."));
    }
    const activity = h("section", { class: "card" }, h("div", { class: "row spread" }, h("h2", null, "Mensajes de participantes en el tiempo", kind("fact", "hecho")), h("div", { class: "row" }, mode, h("label", { class: "row" }, asTable, "Ver como tabla"))), chartBox);
    drawActivity();
    // Ritmo semanal: barras por día y por hora (por defecto) o mapa de calor día × hora
    const weekdayTotal = (wd) => s.weekday[wd] ?? 0;
    const heatBox = h("div");
    const rhythmMode = h("select", { "aria-label": "Vista", onchange: () => drawHeat() }, h("option", { value: "bars" }, "Barras por día y por hora"), h("option", { value: "heat", selected: params.get("ritmo") === "calor" }, "Mapa de calor día × hora"));
    const heatTable = h("input", { type: "checkbox", onchange: () => drawHeat() });
    function drawHeat() {
        if (!m) {
            put(heatBox, h("p", { class: "muted" }, "Sin datos."));
            return;
        }
        const hours = Array.from({ length: 24 }, (_, i) => i);
        if (heatTable.checked) {
            const cell = cellMap(s.weekday_hour);
            put(heatBox, table([{ t: "Día" }, ...hours.map((i) => ({ t: String(i), num: true })), { t: "Total", num: true }], DAY_ORDER.map((wd) => [weekdayName(wd), ...hours.map((i) => n(cell.get(cellKey(wd, i)) ?? 0)), n(weekdayTotal(wd))])));
            return;
        }
        if (rhythmMode.value === "heat") {
            put(heatBox, hourHeatmap(s.weekday_hour));
            return;
        }
        put(heatBox, h("div", { class: "two" }, h("div", null, h("h3", null, "Por día de la semana"), simpleBars(DAY_ORDER.map((d) => ({ label: weekdayName(d), value: weekdayTotal(d), tip: `${weekdayName(d)}: ${n(weekdayTotal(d))} mensajes (${pct(weekdayTotal(d) / m)})` })), { valueLabels: true })), h("div", null, h("h3", null, "Por hora del día"), simpleBars(s.hour.map((v, i) => ({ label: String(i), value: v, tip: `${pad2(i)}:00–${pad2(i)}:59 · ${n(v)} mensajes (${pct(v / m)})` }))))));
    }
    const peakDay = DAY_ORDER.reduce((a, b) => (weekdayTotal(b) > weekdayTotal(a) ? b : a), 1);
    const rhythm = h("section", { class: "card" }, h("div", { class: "row spread" }, h("h2", null, "Cuándo se conversa", kind("fact", "hecho")), h("div", { class: "row" }, rhythmMode, h("label", { class: "row" }, heatTable, "Ver como tabla"))), heatBox, h("small", null, `Hora tal como figura en el archivo, en la zona ${s.chat.timezone}. Denominador: ${n(m)} mensajes de participantes. ` +
        (m ? `Día con más mensajes: ${weekdayName(peakDay)} (${n(weekdayTotal(peakDay))}, ${pct(weekdayTotal(peakDay) / m)}).` : "")));
    drawHeat();
    // Identificadores de autor
    const mergeControl = (a) => {
        const sel = h("select", { "aria-label": "Unir con" }, h("option", { value: "" }, "Unir con…"), state.authors.filter((b) => b.id !== a.id).map((b) => h("option", { value: b.id }, b.label)));
        sel.addEventListener("change", () => void (async () => {
            const target = state.authors.find((b) => b.id === Number(sel.value));
            if (!sel.value || !target)
                return;
            if (!confirm(`¿Tratar «${a.label}» y «${target.label}» como la misma persona?\n\nSolo vos sabés si lo son: nombres parecidos no son prueba. Se puede deshacer.`)) {
                sel.value = "";
                return;
            }
            await api.mergeAuthor(a.id, target.id);
            toast("Alias unidos.");
            await render();
        })());
        return sel;
    };
    const authors = h("section", { class: "card authors" }, h("h2", null, "Identificadores de autor por actividad", kind("fact", "hecho")), h("p", null, h("small", null, au.note)), c.half_rank ? h("p", null, `La mitad de los mensajes la ${c.half_rank === 1 ? "escribió el identificador más activo" : `escribieron los ${n(c.half_rank)} identificadores más activos`}, de ${n(c.curve.length)} que participaron.`) : null, table([{ t: "#", num: true }, { t: "Identificador" }, { t: "Mensajes", num: true }, { t: "% del total", num: true }, { t: "% acumulado", num: true }, { t: "Días activos", num: true }, { t: "Primer / último día" }, { t: "Reconciliar alias" }], au.authors.slice(0, 50).map((a, i) => [n(i + 1),
        h("span", null, h("a", { href: "#/explorar?" + qs({ author_id: a.id }) }, a.label), a.aliases.map((x) => h("div", null, h("small", null, "+ " + x.label + " "), h("button", { class: "ghost small", onclick: () => void api.unmergeAuthor(x.id).then(render) }, "separar")))),
        n(a.messages), pct(a.share), pct(c.curve[i]?.share), n(a.active_days), `${a.first_day} / ${a.last_day}`, mergeControl(a)])), h("small", null, `Se muestran hasta 50 de ${n(au.authors.length)}. «% acumulado» suma esa fila y todas las anteriores. Denominador: ${n(au.denominator)} mensajes de participantes.`));
    const dmax = Math.max(1, ...s.domains.map((d) => d.messages));
    const domains = h("section", { class: "two" }, h("div", { class: "card" }, h("h3", null, "Dominios compartidos", kind("fact", "hecho")), s.domains.length ? hbars(s.domains.slice(0, 15).map((d) => ({
        label: h("a", { href: "#/explorar?" + qs({ q: d.domain, has_link: 1 }) }, d.domain), value: d.messages,
        right: `${n(d.messages)} msj · ${n(d.links)} enlaces`
    })), dmax, n) : h("p", { class: "muted" }, "Sin enlaces en el período."), h("small", null, "«msj» = mensajes distintos que mencionan el dominio; «enlaces» = apariciones.")), h("div", { class: "card" }, h("h3", null, "Registros que no son texto de participantes", kind("fact", "hecho")), h("div", { class: "kv" }, Object.entries(s.system_by_subtype).map(([k, v]) => [h("span", null, "Aviso: " + (SUBTYPE[k] ?? k)), h("span", null, n(v))]), Object.entries(s.media_by_kind).map(([k, v]) => [h("span", null, "Multimedia omitida: " + k), h("span", null, n(v))]), h("span", null, "Mensajes eliminados"), h("span", null, n(t.deleted_messages)), h("span", null, "No reconocidos"), h("span", null, n(t.unrecognized)))));
    const run = cats.run;
    const overview = h("section", { class: "two" }, h("div", { class: "card" }, h("h3", null, "Categorías", kind("auto", "clasificación automática")), run ? h("p", null, h("small", null, `Proveedor: ${PROVIDER[run.provider]} (${run.model}). ${n(cats.classified_messages)} de ${n(cats.participant_messages)} mensajes clasificados. Estimaciones, no conteos oficiales.`))
        : h("p", { class: "muted" }, "Todavía no se ejecutó ninguna clasificación."), run ? hbars(categories().map((k) => ({
        label: h("a", { href: "#/explorar?" + qs({ category: k.key }) }, k.label), value: cats.effective[k.key]
    })), Math.max(1, ...Object.values(cats.effective)), n) : null, h("p", null, h("a", { href: "#/categorias" }, run ? "Ver y revisar categorías →" : "Ir a clasificar →"))), h("div", { class: "card" }, h("h3", null, "Para revisar", kind("pending", "interpretación pendiente")), h("div", { class: "kv" }, h("span", null, "Registros marcados para revisión"), h("span", null, n(t.flagged_for_review)), h("span", null, "Registros no reconocidos"), h("span", null, n(t.unrecognized)), h("span", null, "Clasificaciones corregidas a mano"), h("span", null, n(cats.corrected_messages))), t.flagged_for_review ? h("p", null, h("a", { href: "#/explorar?" + qs({ review_only: 1, record_type: "all" }) }, "Ver registros marcados →")) : null, h("p", null, h("small", null, "Las cifras describen actividad visible en el grupo. No permiten inferir intenciones ni quién originó una idea, y «sin seguimiento visible» no significa que no hubo seguimiento por privado."))));
    put(view, facts, activity, rhythm, overview, authors, domains);
}
// ----------------------------------------------------------------- mensajes --
/** El backend delimita coincidencias con U+0001 … U+0002; se convierten en <mark> con nodos de texto. */
function snippetNodes(snippet) {
    const out = [];
    for (const part of snippet.split("\u0001")) {
        const i = part.indexOf("\u0002");
        if (i === -1)
            out.push(part);
        else
            out.push(h("mark", null, part.slice(0, i)), part.slice(i + 1));
    }
    return out;
}
function reference(m) {
    const quote = m.text.replace(/[‎‏‪-‬⁦-⁩]/g, "").trim();
    const src = m.sources[0];
    const lines = src ? ` · ${src.file_name}, línea${src.line_end > src.line_start ? "s" : ""} ${src.line_start}${src.line_end > src.line_start ? "–" + src.line_end : ""}` : "";
    return `«${quote.length > 400 ? quote.slice(0, 400) + "…" : quote}»\n— ${m.author_label ?? "Aviso del sistema"}, ${m.chat_name}, ${m.ts_local.slice(0, 16)}` +
        `\nRef. ${m.ref}${lines}` +
        `\n${location.origin}/#/m/${m.id} (enlace interno de Community Lens; WhatsApp no ofrece enlaces a mensajes en el TXT)`;
}
function messageCard(m, opts = {}) {
    const { target = false, full = false, onChange = render } = opts;
    const isMsg = m.record_type === "message";
    const body = h("div", { class: "body" + (full ? "" : " clamp") }, m.snippet && !full ? snippetNodes(m.snippet) : m.text);
    const assigned = m.auto.filter((a) => a.assigned);
    const chips = [];
    if (m.human) {
        m.human.categories.forEach((c) => chips.push(h("span", { class: "chip human", title: "Corrección humana" }, catLabel(c) + " · revisado")));
        if (assigned.length)
            chips.push(h("span", { class: "chip", title: "Predicción original conservada" }, "automático decía: " + assigned.map((a) => catLabel(a.category)).join(", ")));
    }
    else {
        assigned.forEach((a) => chips.push(h("span", {
            class: "chip auto",
            title: a.evidence?.rules ? "Reglas: " + (a.evidence.rules[a.category] ?? []).join(", ") : "Clasificación automática",
        }, `${catLabel(a.category)} · ${PROVIDER[a.provider]}` + (a.score != null ? ` · noul ${a.score.toFixed(2)}` : ""))));
    }
    if (m.needs_review)
        chips.push(h("span", { class: "chip flag" }, "revisar: " + (m.review_reason ? REVIEW[m.review_reason] ?? m.review_reason : "sin motivo")));
    if (m.media_kind)
        chips.push(h("span", { class: "chip" }, "adjunto no disponible: " + m.media_kind));
    if (m.is_deleted)
        chips.push(h("span", { class: "chip" }, "eliminado"));
    if (m.is_edited)
        chips.push(h("span", { class: "chip" }, "editado"));
    if (!isMsg)
        chips.push(h("span", { class: "chip" }, m.record_type === "system" ? "aviso del sistema" : "no reconocido"));
    const editor = h("div", { hidden: true });
    const edit = () => {
        const boxes = categories().map((c) => ({ key: c.key, input: h("input", { type: "checkbox", checked: m.effective.includes(c.key) }), label: c.label }));
        const save = async (cats) => {
            await api.setLabels(m.id, cats);
            toast(cats ? "Corrección guardada; la predicción original se conserva." : "Se volvió a la clasificación automática.");
            await onChange();
        };
        put(editor, h("fieldset", null, h("legend", null, "Categorías correctas para este mensaje"), h("div", { class: "checks" }, boxes.map((b) => h("label", { class: "row" }, b.input, b.label))), h("div", { class: "actions" }, h("button", { class: "small", onclick: () => void save(boxes.filter((b) => b.input.checked).map((b) => b.key)) }, "Guardar corrección"), m.human ? h("button", { class: "ghost small", onclick: () => void save(null) }, "Volver a la automática") : null, h("button", { class: "ghost small", onclick: () => { editor.hidden = true; } }, "Cancelar"))));
        editor.hidden = false;
    };
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(reference(m));
            toast("Referencia copiada.");
        }
        catch {
            prompt("Copiá la referencia:", reference(m));
        }
    };
    return h("article", { class: "msg" + (target ? " target" : "") + (isMsg ? "" : " system") }, h("div", { class: "meta" }, h("b", null, m.author_label ?? "Sistema"), m.canonical_label ? h("span", null, `(alias de ${m.canonical_label})`) : null, h("span", null, m.ts_local.slice(0, 16)), h("span", null, m.chat_name)), body, chips.length ? h("div", { class: "chips" }, chips) : null, h("div", { class: "actions" }, target ? null : h("a", { href: `#/m/${m.id}`, class: "btnlink" }, "Ver contexto"), h("button", { class: "ghost small", onclick: () => void copy() }, "Copiar referencia"), isMsg && m.text ? h("button", { class: "ghost small", onclick: edit }, "Corregir categorías") : null), editor);
}
function readFilters(params) {
    const text = (k, fallback = "") => params.get(k) ?? fallback;
    const flag = (k) => Boolean(params.get(k));
    return {
        q: text("q"), author_id: text("author_id"), category: text("category"), record_type: text("record_type", "message"), order: text("order", "recent"),
        has_link: flag("has_link"), has_media: flag("has_media"), review_only: flag("review_only"), human_only: flag("human_only"), all_chats: flag("all_chats"),
    };
}
async function viewExplorar(params) {
    const f = readFilters(params);
    if (state.chatId != null && !state.authors.length)
        state.authors = (await api.getAuthors({ chat_id: state.chatId, from: "", to: "" })).authors;
    const select = (label, current, options) => h("select", { "aria-label": label }, options.map(([v, t]) => h("option", { value: v, selected: current === v }, t)));
    const check = (checked) => h("input", { type: "checkbox", checked });
    const q = h("input", { value: f.q, type: "search", placeholder: "Buscar texto… (usá \"comillas\" para frases, term* para prefijos)", "aria-label": "Buscar" });
    const author = select("Autor", f.author_id, [["", "Todos"], ...state.authors.map((a) => [String(a.id), `${a.label} (${n(a.messages)})`])]);
    const category = select("Categoría", f.category, [["", "Todas"], ...categories().map((c) => [c.key, c.label])]);
    const rtype = select("Tipo de registro", f.record_type, [["message", "Mensajes de participantes"], ["system", "Avisos del sistema"], ["unrecognized", "No reconocidos"], ["all", "Todos los registros"]]);
    const order = select("Orden", f.order, [["recent", "Más recientes"], ["oldest", "Más antiguos"], ["relevance", "Relevancia"]]);
    const cLink = check(f.has_link), cMedia = check(f.has_media), cRev = check(f.review_only), cHum = check(f.human_only), cAll = check(f.all_chats);
    const results = h("div"), status = h("p", { class: "muted" });
    const apply = (e) => {
        e?.preventDefault();
        location.hash = "#/explorar?" + qs({
            q: q.value.trim(), author_id: author.value, category: category.value,
            record_type: rtype.value === "message" ? "" : rtype.value, order: order.value === "recent" ? "" : order.value,
            has_link: cLink.checked && 1, has_media: cMedia.checked && 1, review_only: cRev.checked && 1, human_only: cHum.checked && 1, all_chats: cAll.checked && 1,
        });
    };
    const form = h("form", { class: "card", onsubmit: apply }, h("h2", null, "Explorar mensajes"), h("div", { class: "filters" }, h("div", { class: "wide row" }, q, h("button", { type: "submit" }, "Buscar")), h("label", { class: "field" }, "Autor (identificador)", author), h("label", { class: "field" }, "Categoría", category), h("label", { class: "field" }, "Tipo de registro", rtype), h("label", { class: "field" }, "Orden", order)), h("div", { class: "checks" }, h("label", { class: "row" }, cLink, "Con enlaces"), h("label", { class: "row" }, cMedia, "Con multimedia omitida"), h("label", { class: "row" }, cRev, "Marcados para revisión"), h("label", { class: "row" }, cHum, "Corregidos a mano"), h("label", { class: "row" }, cAll, "Buscar en todos los grupos")), h("small", null, "Grupo y fechas se eligen arriba. La categoría usa la corrección humana si existe; si no, la clasificación automática vigente."));
    q.style.flex = "1";
    q.style.minWidth = "180px";
    [author, category, rtype, order, cLink, cMedia, cRev, cHum, cAll].forEach((el) => el.addEventListener("change", apply));
    put(view, form, h("section", null, status, results));
    let offset = 0;
    async function load() {
        const r = await api.search({
            ...(f.all_chats ? { from: state.from, to: state.to } : scope()),
            q: f.q, author_id: f.all_chats ? "" : f.author_id, category: f.category, record_type: f.record_type,
            has_link: f.has_link, has_media: f.has_media, review_only: f.review_only, human_only: f.human_only, order: f.order, limit: 40, offset,
        });
        status.textContent = `${n(r.total)} resultado${r.total === 1 ? "" : "s"}` + (f.q ? ` para «${f.q}»` : "") + (f.all_chats ? " en todos los grupos" : "");
        results.querySelector(".more")?.remove();
        const reload = () => { offset = 0; put(results); return load(); };
        r.items.forEach((m) => results.append(messageCard(m, { onChange: reload })));
        offset += r.items.length;
        if (offset < r.total)
            results.append(h("p", { class: "more" }, h("button", { class: "ghost", onclick: () => void load() }, `Cargar más (${n(r.total - offset)} restantes)`)));
        if (!r.total)
            results.append(h("p", { class: "muted" }, "Sin resultados con estos filtros."));
    }
    await load();
}
// ----------------------------------------------------------------- contexto --
let lastHash = "#/resumen";
function closeContext() {
    drawer.hidden = true;
    if (location.hash.startsWith("#/m/"))
        history.replaceState(null, "", lastHash);
}
async function openContext(id) {
    let before = 6, after = 6;
    const closeButton = () => h("button", { class: "ghost", onclick: closeContext }, "Cerrar");
    async function draw() {
        const r = await api.getContext(id, before, after);
        const target = r.items.find((m) => m.id === id);
        if (!target)
            throw new Error("El mensaje no aparece en su propio contexto");
        put(drawer, h("header", null, h("h2", null, "Contexto del mensaje"), closeButton()), h("p", null, h("small", null, `${target.chat_name} · Ref. ${target.ref} · posición ${n(target.seq + 1)} en la secuencia. `, target.sources.map((s) => `${s.file_name}, líneas ${s.line_start}–${s.line_end}`).join(" · "), ". ", r.note)), h("p", null, h("button", { class: "ghost small", onclick: () => { before += 10; void draw(); } }, "↑ Ver anteriores")), r.items.map((m) => messageCard(m, { target: m.id === id, full: true, onChange: draw })), h("p", null, h("button", { class: "ghost small", onclick: () => { after += 10; void draw(); } }, "↓ Ver siguientes")));
        drawer.querySelector(".target")?.scrollIntoView({ block: "center" });
    }
    drawer.hidden = false;
    try {
        await draw();
    }
    catch (e) {
        put(drawer, h("p", { class: "error" }, errMsg(e)), closeButton());
    }
}
// ----------------------------------------------------------------- recursos --
const minute = (ts) => ts.slice(0, 16);
const people = (names) => names.length <= 3 ? names.join(", ") : `${names.slice(0, 3).join(", ")} y ${n(names.length - 3)} más`;
function documentCard(d) {
    const v = d.latest;
    const versionTag = (version) => (version ? `v${version}` : "sin número de versión");
    return h("article", { class: "msg" }, h("div", { class: "meta" }, h("b", null, d.title), h("span", null, `${n(d.shares)} envío${d.shares === 1 ? "" : "s"}`), h("span", null, `por ${people(d.sharers) || "—"}`)), h("div", { class: "body" }, d.versions.length > 1 ? h("span", { class: "chip human" }, "última versión") : null, " ", h("a", { href: `#/m/${v.message_id}` }, v.name), ` · ${versionTag(v.version)} · ${minute(v.ts_local)} · ${v.author ?? "—"}`), d.latest_is_most_recent ? null
        : h("div", { class: "notice" }, `Ojo: lo último que se envió (${minute(d.last_shared)}) fue una versión anterior a la ${versionTag(v.version)}.`), d.versions.length > 1 ? h("details", null, h("summary", null, `Todas las versiones (${d.versions.length})`), table([{ t: "Versión" }, { t: "Archivo" }, { t: "Último envío" }, { t: "Por" }, { t: "Veces", num: true }], d.versions.map((x) => [versionTag(x.version), h("a", { href: `#/m/${x.message_id}` }, x.name), minute(x.ts_local), x.author ?? "—", n(x.times_shared)]))) : null, h("div", { class: "actions" }, h("a", { href: `#/m/${v.message_id}`, class: "btnlink" }, "Ver en la conversación")));
}
function linkCard(l) {
    return h("article", { class: "msg" }, h("div", { class: "meta" }, h("b", null, l.domain), h("span", null, `${n(l.shares)} mensaje${l.shares === 1 ? "" : "s"}`), h("span", null, `último: ${minute(l.last_shared)} · ${l.author ?? "—"}`)), 
    // El enlace externo no se abre solo: rel="noopener noreferrer" y pestaña nueva.
    h("div", { class: "body" }, h("a", { href: l.url, target: "_blank", rel: "noopener noreferrer" }, l.url)), h("div", { class: "actions" }, h("a", { href: `#/m/${l.message_id}`, class: "btnlink" }, "Ver en la conversación")));
}
async function viewRecursos(params) {
    if (state.chatId == null)
        return emptyState();
    const kindParam = params.get("tipo") === "enlaces" ? "link" : "document";
    const orderParam = params.get("orden") === "compartidos" ? "shared" : "recent";
    const q = h("input", { value: params.get("q") ?? "", type: "search", placeholder: "Nombre del archivo, dominio o quién lo compartió…", "aria-label": "Buscar recurso" });
    const kindSel = h("select", { "aria-label": "Tipo" }, h("option", { value: "document", selected: kindParam === "document" }, "Documentos"), h("option", { value: "link", selected: kindParam === "link" }, "Enlaces"));
    const orderSel = h("select", { "aria-label": "Orden" }, h("option", { value: "recent", selected: orderParam === "recent" }, "Más recientes"), h("option", { value: "shared", selected: orderParam === "shared" }, "Más compartidos"));
    const apply = (e) => {
        e?.preventDefault();
        location.hash = "#/recursos?" + qs({ q: q.value.trim(), tipo: kindSel.value === "link" ? "enlaces" : "", orden: orderSel.value === "shared" ? "compartidos" : "" });
    };
    kindSel.addEventListener("change", apply);
    orderSel.addEventListener("change", apply);
    q.style.flex = "1";
    q.style.minWidth = "180px";
    const status = h("p", { class: "muted" }), results = h("div"), note = h("small");
    put(view, h("form", { class: "card", onsubmit: apply }, h("h2", null, "Recursos compartidos", kind("fact", "hecho")), h("p", null, h("small", null, "Documentos y enlaces que se compartieron en el grupo. Los documentos se agrupan por nombre, con la última versión arriba.")), h("div", { class: "row end" }, q, h("label", { class: "field" }, "Tipo", kindSel), h("label", { class: "field" }, "Orden", orderSel), h("button", { type: "submit" }, "Buscar")), note), h("section", null, status, results));
    let offset = 0;
    async function load() {
        const r = await api.getResources(scope(), kindParam, params.get("q") ?? "", orderParam, offset);
        const c = r.counts;
        status.textContent = kindParam === "document"
            ? `${n(r.total)} documento${r.total === 1 ? "" : "s"}` + (params.get("q") ? ` para «${params.get("q") ?? ""}»` : "") +
                ` · en el período: ${n(c.documents)} documentos distintos en ${n(c.document_shares)} envíos` + (c.unnamed_documents ? ` · ${n(c.unnamed_documents)} sin nombre de archivo, no se pueden listar` : "")
            : `${n(r.total)} enlace${r.total === 1 ? "" : "s"}` + (params.get("q") ? ` para «${params.get("q") ?? ""}»` : "") + ` · en el período: ${n(c.links)} enlaces distintos`;
        note.textContent = r.note;
        results.querySelector(".more")?.remove();
        r.items.forEach((it) => results.append(it.kind === "document" ? documentCard(it) : linkCard(it)));
        offset += r.items.length;
        if (offset < r.total)
            results.append(h("p", { class: "more" }, h("button", { class: "ghost", onclick: () => void load() }, `Cargar más (${n(r.total - offset)} restantes)`)));
        if (!r.total)
            results.append(h("p", { class: "muted" }, "Sin resultados."));
    }
    await load();
}
// --------------------------------------------------------------- categorías --
async function viewCategorias() {
    const chatId = state.chatId;
    if (chatId == null)
        return emptyState();
    const [cats, runs, monthly, cfg] = await Promise.all([api.getCategorySummary(scope()), api.getRuns(chatId), api.getCategoryMonthly(scope()), api.getConfig()]);
    state.config = cfg;
    const taxo = cfg.taxonomy.categories;
    const jev = cfg.providers.find((p) => p.name === "jev");
    const run = cats.run;
    const busy = h("span", { class: "muted" });
    const start = async (provider, consent) => {
        busy.textContent = "Clasificando…";
        try {
            const r = await api.startRun(scope(), provider, consent);
            if (!r.activated)
                alert(`Ejecución #${r.run_id} guardada: clasificó ${n(r.classified)} de ${n(r.eligible)} mensajes.\n\nComo cubre menos que la clasificación vigente (#${r.active_run_id}), NO la reemplazó. Podés verla o activarla en "Ejecuciones guardadas", más abajo.`);
            else
                toast(`Ejecución #${r.run_id}: ${n(r.classified)} mensajes clasificados` + (r.errors ? `, ${n(r.errors)} con error` : "") + (r.truncated ? ` (tope alcanzado; había ${n(r.eligible)})` : ""));
            await render();
        }
        catch (e) {
            busy.textContent = "";
            alert(errMsg(e));
        }
    };
    const max = Math.max(1, ...Object.values(cats.effective));
    const summary = h("section", { class: "card" }, h("h2", null, "Categorías del período", kind("auto", "clasificación automática")), run ? h("p", null, h("small", null, `Ejecución #${run.id} · proveedor: ${run.provider === "rules" ? "reglas locales (heurística por patrones, sin IA)" : "Jev de TypeSafe"} · versión/modelo ${run.model} · taxonomía ${run.taxonomy_version} · ${utcMinute(run.started_at)} UTC. `, `${n(cats.classified_messages)} de ${n(cats.participant_messages)} mensajes de participantes tienen clasificación; ${n(cats.corrected_messages)} corregidos a mano. `, cats.note))
        : h("div", { class: "notice" }, "Aún no hay clasificación para este grupo. Ejecutá el proveedor de reglas: es local y no requiere credenciales."), run ? taxo.map((c) => h("div", null, hbars([{ label: h("a", { href: "#/explorar?" + qs({ category: c.key }) }, c.label), value: cats.effective[c.key],
            right: n(cats.effective[c.key]) + (cats.human[c.key] ? ` (${n(cats.human[c.key])} por revisión humana)` : "") }], max, n), h("small", null, c.definition))) : null, run?.provider === "jev" && jev?.score_meaning ? h("p", null, h("small", null, jev.score_meaning)) : null, run?.provider === "rules" ? h("p", null, h("small", null, "Las reglas reconocen formas frecuentes de escribir; no entienden significado. Pasá el cursor sobre una etiqueta en Explorar para ver qué regla se disparó.")) : null);
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
    if (run)
        drawTrend();
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
        await render();
    };
    const runBox = h("section", { class: "card" }, h("h2", null, "Ejecutar clasificación"), h("p", null, h("small", null, "Se clasifican los mensajes de participantes con texto del grupo y período elegidos arriba. Cada ejecución queda registrada; no reemplaza a las anteriores ni a tus correcciones.")), h("div", { class: "row" }, h("button", { onclick: () => void start("rules", false) }, "Clasificar con reglas (local)"), busy), jevBox, h("p", null, h("small", null, "OpenAI Decisions: espacio reservado en el código, no implementado.")), runs.length ? h("details", { open: runs.length > 1 }, h("summary", null, `Ejecuciones guardadas (${runs.length})`), h("p", null, h("small", null, "Ninguna ejecución borra a otra. La «vigente» es la que alimenta las categorías en toda la app; podés cambiarla cuando quieras.")), table([{ t: "#" }, { t: "Vigente" }, { t: "Proveedor" }, { t: "Modelo" }, { t: "Período" }, { t: "Estado" }, { t: "Clasificó", num: true }, { t: "Errores", num: true }, { t: "Inicio (UTC)" }], runs.map((r) => [r.id,
        r.active ? h("span", { class: "chip human" }, "vigente")
            : r.status === "completed" || r.status === "partial" ? h("button", { class: "ghost small", onclick: () => void activate(r.id) }, "Usar esta") : "—",
        PROVIDER[r.provider], r.model, `${r.date_from ?? "inicio"} → ${r.date_to ?? "fin"}`, r.status + (r.error ? ": " + r.error : ""),
        n(r.n_messages) + (r.config.eligible_messages ? " de " + n(r.config.eligible_messages) : ""), n(r.n_errors), utcMinute(r.started_at)]))) : null);
    const review = h("section", { class: "card" }, h("h2", null, "Revisión humana", kind("pending", "interpretación pendiente")), h("p", null, h("small", null, "Una categoría automática es una hipótesis hasta que alguien la revisa. Corregir agrega tu etiqueta sin borrar la predicción original.")), h("div", { class: "row" }, h("a", { href: "#/explorar?" + qs({ category: "oferta_de_colaboracion" }) }, "Revisar ofertas de colaboración"), "·", h("a", { href: "#/explorar?" + qs({ category: "propuesta_de_proyecto" }) }, "Revisar propuestas"), "·", h("a", { href: "#/explorar?" + qs({ human_only: 1 }) }, `Ver corregidos a mano (${n(cats.corrected_messages)})`), "·", h("a", { href: "#/explorar?" + qs({ review_only: 1, record_type: "all" }) }, "Registros marcados al importar")));
    put(view, summary, run ? trend : null, review, runBox);
}
// ----------------------------------------------------------------- importar --
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
async function viewImportar() {
    const out = h("div");
    const file = h("input", { type: "file", accept: ".txt,text/plain" });
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
            timezone: tz.value, date_order: order, confirm_low_overlap: confirmLow,
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
                state.chatId = r.chat_id;
                state.authors = [];
                syncScope();
                const text = r.status === "duplicate" ? r.message
                    : `Importado. ${n(r.reconciliation.added)} registros nuevos; ${n(r.reconciliation.matched_existing)} ya estaban (solo se anotó la nueva fuente); ${n(r.reconciliation.only_in_previous)} existen solo en importaciones previas y se conservaron; ${n(r.reconciliation.ambiguous_pairs)} quedaron marcados como solape ambiguo. `;
                put(msg, h("div", { class: "notice" }, text, " ", h("a", { href: "#/resumen" }, "Ver resumen →")));
                await renderChatsAdmin();
            }
            catch (e) {
                put(msg, h("p", { class: "error" }, errMsg(e)));
                go.disabled = false;
            }
        })());
        put(out, h("h3", null, "Diagnóstico de importación", kind("fact", "hecho")), diagRows(d), d.warnings.map((w) => h("div", { class: "notice" }, w)), d.issues.length ? h("details", null, h("summary", null, `Líneas no convertidas en registro (${d.issues.length})`), table(ISSUE_COLUMNS, issueRows(d.issues))) : null, dateBox, h("fieldset", null, h("legend", null, "Destino"), h("p", null, h("small", null, rec.action === "update" ? "Sugerencia: actualización de un grupo existente. " : rec.action === "duplicate" ? "Archivo ya importado. " : "Sugerencia: grupo nuevo. ", rec.reason)), h("div", { class: "row end" }, h("label", { class: "field" }, "Importar en", dest), nameField), h("p", null, h("small", null, "Al actualizar, los mensajes se alinean por secuencia y contexto: no se borran mensajes existentes y las repeticiones legítimas se conservan."))), h("div", { class: "row" }, go), msg);
    }
    file.addEventListener("change", () => { buf = null; void preview(""); });
    async function renderChatsAdmin() {
        await loadChats();
        const blocks = await Promise.all(state.chats.map(async (c) => {
            const imps = await api.getChatImports(c.id);
            const tzIn = h("input", { value: c.timezone, list: "tzlist", size: 22 });
            const remove = async () => {
                if (!confirm(`¿Eliminar «${c.name}» y todos sus datos derivados (mensajes, índice de búsqueda, clasificaciones y correcciones)?\n\nNo se puede deshacer. Tus archivos TXT originales no se tocan.`))
                    return;
                const r = await api.deleteChat(c.id);
                toast(`Eliminado: ${n(r.records_removed)} registros.`);
                if (state.chatId === c.id)
                    state.chatId = null;
                await loadChats();
                syncScope();
                await renderChatsAdmin();
            };
            const saveZone = async () => {
                try {
                    await api.updateChatTimezone(c.id, tzIn.value);
                    toast("Zona actualizada; fechas recalculadas.");
                    await renderChatsAdmin();
                }
                catch (e) {
                    alert(errMsg(e));
                }
            };
            return h("div", { class: "card" }, h("div", { class: "row spread" }, h("h3", null, c.name), h("button", { class: "danger small", onclick: () => void remove() }, "Eliminar grupo y datos")), h("p", null, h("small", null, `${n(c.participant_messages)} mensajes de participantes · ${c.first_record?.slice(0, 10) ?? "—"} → ${c.last_record?.slice(0, 10) ?? "—"}`)), h("div", { class: "row end" }, h("label", { class: "field" }, "Zona horaria del chat", tzIn), h("button", { class: "ghost small", onclick: () => void saveZone() }, "Guardar zona")), h("small", null, "El TXT no indica zona horaria: las horas son las del teléfono que exportó. Esta zona declara cómo interpretarlas."), imps.map((i) => h("details", null, h("summary", null, `Importación #${i.id}: ${i.file_name} · ${utcMinute(i.imported_at)} UTC`), h("p", null, h("small", null, `Fecha ${i.date_order} (${i.date_order_source === "detected" ? "detectada" : "elegida por vos"}) · zona ${i.timezone} · SHA-256 ${i.file_sha256.slice(0, 12)}… · ` +
                `reconciliación: ${n(i.reconciliation.added)} nuevos, ${n(i.reconciliation.matched_existing)} ya existentes, ${n(i.reconciliation.ambiguous_pairs)} ambiguos (precisión: ${i.reconciliation.precision}).`)), diagRows(i.diagnostics), i.issues.length ? table(ISSUE_COLUMNS, issueRows(i.issues)) : null)));
        }));
        put(admin, h("h2", null, "Grupos importados"), blocks.length ? blocks : h("p", { class: "muted" }, "Todavía no hay grupos."));
    }
    put(view, h("section", { class: "card" }, h("h2", null, "Importar exportación de WhatsApp (.txt)"), h("p", null, h("small", null, "El archivo se procesa en esta máquina y se guarda en una base SQLite local. El original no se modifica. Reimportar el mismo archivo no duplica nada.")), h("div", { class: "row end" }, h("label", { class: "field" }, "Archivo TXT", file), h("label", { class: "field" }, "Zona horaria de las horas del archivo", tz), tzlist), out), h("section", null, admin));
    await renderChatsAdmin();
}
// ------------------------------------------------------------------ reporte --
async function viewReporte() {
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
}
// ------------------------------------------------------------------- router --
const TABS = ["resumen", "explorar", "recursos", "categorias", "importar", "reporte"];
const isTab = (x) => TABS.some((t) => t === x);
function emptyState() {
    put(view, h("section", { class: "card" }, h("h2", null, "Todavía no hay grupos importados"), h("p", null, "Empezá importando una exportación TXT de WhatsApp."), h("a", { href: "#/importar", class: "btnlink" }, "Importar un chat")));
}
async function loadChats() {
    state.chats = await api.getChats();
    if (!state.chats.some((c) => c.id === state.chatId))
        state.chatId = state.chats[0]?.id ?? null;
}
function syncScope() {
    const sel = byId("chat", HTMLSelectElement);
    put(sel, state.chats.map((c) => h("option", { value: c.id, selected: c.id === state.chatId }, c.name)));
    sel.disabled = !state.chats.length;
    byId("from", HTMLInputElement).value = state.from;
    byId("to", HTMLInputElement).value = state.to;
    try {
        localStorage.setItem("cl.chat", state.chatId == null ? "" : String(state.chatId));
    }
    catch { /* almacenamiento no disponible */ }
}
let routeToken = 0;
async function route(hash) {
    const [path, query] = hash.slice(2).split("?");
    const tab = isTab(path) ? path : "resumen";
    const params = new URLSearchParams(query ?? "");
    document.querySelectorAll("#tabs a[data-tab]").forEach((a) => a.classList.toggle("on", a.dataset["tab"] === tab));
    const views = {
        resumen: () => viewResumen(params), explorar: () => viewExplorar(params),
        recursos: () => viewRecursos(params), categorias: viewCategorias, importar: viewImportar, reporte: viewReporte,
    };
    const mine = ++routeToken;
    try {
        await views[tab]();
    }
    catch (e) {
        // Solo se muestra el error de la navegación más reciente.
        if (mine === routeToken)
            put(view, h("section", { class: "card" }, h("p", { class: "error" }, "Error: " + errMsg(e))));
    }
}
async function render() {
    const hash = location.hash || "#/resumen";
    const m = /^#\/m\/(\d+)/.exec(hash);
    if (m?.[1]) {
        if (!view.childNodes.length)
            await route("#/explorar");
        await openContext(Number(m[1]));
        return;
    }
    drawer.hidden = true;
    lastHash = hash;
    await route(hash);
}
async function init() {
    const cfg = await api.getConfig();
    state.config = cfg;
    if (!cfg.loopback) {
        const badge = byId("badge", HTMLElement);
        badge.textContent = "expuesta en red";
        badge.classList.add("error");
    }
    try {
        state.chatId = Number(localStorage.getItem("cl.chat")) || null;
    }
    catch { /* sin almacenamiento */ }
    await loadChats();
    syncScope();
    const chatSel = byId("chat", HTMLSelectElement), from = byId("from", HTMLInputElement), to = byId("to", HTMLInputElement);
    chatSel.addEventListener("change", () => { state.chatId = Number(chatSel.value) || null; state.authors = []; syncScope(); void render(); });
    from.addEventListener("change", () => { state.from = from.value; void render(); });
    to.addEventListener("change", () => { state.to = to.value; void render(); });
    byId("allTime", HTMLButtonElement).addEventListener("click", () => { state.from = ""; state.to = ""; syncScope(); void render(); });
    window.addEventListener("hashchange", () => void render());
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !drawer.hidden)
        closeContext(); });
    if (!state.chats.length && !location.hash)
        location.hash = "#/importar";
    else
        await render();
}
init().catch((e) => {
    put(view, h("section", { class: "card" }, h("p", { class: "error" }, "No se pudo iniciar la app: " + errMsg(e))));
});
