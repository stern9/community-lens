// Resumen: las cifras oficiales del grupo y período. Todo sale de /api/stats; aquí solo se presenta.
// Cada función de abajo arma una sección de la pantalla, en el orden en que aparecen.
import * as api from "../api.js";
import { qs } from "../api.js";
import { calendarChart, cellKey, cellMap, hourHeatmap, monthChart, simpleBars } from "../charts.js";
import { h, hbars, kind, put, table } from "../dom.js";
import { DAY_ORDER, dec, monthLabel, n, pad2, pct, weekdayName } from "../format.js";
import { PROVIDER, SUBTYPE } from "../labels.js";
import { emptyState, toast, view } from "../shell.js";
import { categories, mergeAuthor, scope, state, unmergeAuthor } from "../state.js";
export const viewResumen = async ({ params, refresh }) => {
    if (state.chatId == null)
        return emptyState();
    const [s, au, cats] = await Promise.all([api.getStats(scope()), api.getAuthors(scope()), api.getCategorySummary(scope())]);
    put(view, factsCard(s), activityCard(s, params.get("vista") === "dia"), rhythmCard(s, params.get("ritmo") === "calor"), categoriesAndReviewCards(s, cats), authorsCard(s, au, refresh), domainsAndRecordsCards(s));
};
/** Métricas con su denominador, meses parciales, días sin mensajes y definiciones. */
function factsCard(s) {
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
    return h("section", { class: "card" }, h("h2", null, "Resumen del período", kind("fact", "hechos calculados")), h("p", { class: "muted" }, s.period.first_day
        ? `${s.period.first_day} → ${s.period.last_day ?? ""} · ${n(t.calendar_days)} días calendario · zona ${s.chat.timezone}. Historial importado: ${s.coverage.first_record?.slice(0, 10) ?? "—"} → ${s.coverage.last_record?.slice(0, 10) ?? "—"}.`
        : "No hay registros en el período elegido."), h("div", { class: "grid" }, metric("Mensajes de participantes", n(m), `de ${n(t.records)} registros; ${n(t.system_notices)} avisos del sistema no cuentan`), metric("Identificadores de autor activos", n(t.active_authors), "nombres o teléfonos tal como aparecen; no son «miembros»"), metric("Días activos", n(t.active_days), `de ${n(t.conversation_days)} días desde el primer mensaje · ${n(t.silent_days)} sin mensajes`), metric("Promedio de mensajes por día", dec(av.per_day), `${n(m)} mensajes en ${n(av.per_day_denominator)} días de conversación`), metric("Promedio de mensajes por mes", dec(av.per_month), av.per_month_denominator
        ? `${n(av.per_month_messages)} mensajes en ${av.per_month_denominator} meses completos; sin los parciales`
        : "no hay meses completos en el período"), metric("Promedio de mensajes por hora", dec(av.per_hour, 2), av.peak_hour != null
        ? `contando las 24 horas · hora pico ${pad2(av.peak_hour)}:00, con ${dec(av.peak_hour_per_day)} por día`
        : "sin mensajes"), metric("Mensajes con enlaces", n(t.messages_with_links), of(t.messages_with_links)), metric("Enlaces en total", n(t.total_links), `en ${n(t.messages_with_links)} mensajes · ${n(t.distinct_domains)} dominios`), metric("Multimedia omitida", n(t.media_omitted_messages), of(t.media_omitted_messages)), metric(`Los ${c.top5.n} participantes más activos`, pct(c.top5.share), `escribieron ${n(c.top5.messages)} de ${n(m)} mensajes`), metric(`Los ${c.top10.n} participantes más activos`, pct(c.top10.share), `escribieron ${n(c.top10.messages)} de ${n(m)} mensajes`)), h("p", null, h("small", null, "«Más activos» se mide por cantidad de mensajes, no por la calidad ni el valor de los aportes. ", `Además: ${n(t.deleted_messages)} mensajes eliminados, ${n(t.edited_messages)} editados, ${n(t.polls)} encuestas.`)), partial.length ? h("div", { class: "notice" }, "Meses parciales: ", partial.map((x) => `${monthLabel(x.month)} (${x.days_covered} de ${x.days_in_month} días)`).join(", "), ". No son comparables con meses completos. El último día del historial termina al momento de exportar.") : null, silentDays.length ? h("details", null, h("summary", null, `Días sin mensajes (${n(silentDays.length)} de ${n(t.conversation_days)})`), h("p", null, silentDays.map((d) => `${weekdayName(d.weekday).toLowerCase()} ${d.day}`).join(" · ")), h("small", null, `${n(weekendSilent)} de ${n(silentDays.length)} cayeron en sábado o domingo. Se cuenta desde el primer mensaje de un participante (${s.conversation.first_day ?? "—"}), no desde la creación del grupo; en esos días puede haber avisos del sistema.`)) : null, h("p", null, h("small", null, "¿De dónde sale cada cifra? ", h("a", { href: "/como-funciona", target: "_blank", rel: "noopener" }, "Ver el flujo de datos de la app ↗"))), h("details", null, h("summary", null, "Definiciones y denominadores"), h("dl", { class: "defs" }, definitionList.map(([term, text]) => [h("dt", null, term), h("dd", null, text)]))));
}
/** Mensajes en el tiempo: por mes o calendario diario, como gráfico o como tabla. */
function activityCard(s, startDaily) {
    const partial = s.monthly.filter((x) => x.partial);
    const chartBox = h("div");
    const mode = h("select", { "aria-label": "Vista", onchange: () => drawActivity() }, h("option", { value: "month" }, "Por mes"), h("option", { value: "day", selected: startDaily }, "Calendario diario"));
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
    return activity;
}
/** Cuándo se conversa: por día de la semana y por hora, o el cruce de ambos. */
function rhythmCard(s, startHeat) {
    const m = s.totals.participant_messages;
    const weekdayTotal = (wd) => s.weekday[wd] ?? 0;
    const heatBox = h("div");
    const rhythmMode = h("select", { "aria-label": "Vista", onchange: () => drawHeat() }, h("option", { value: "bars" }, "Barras por día y por hora"), h("option", { value: "heat", selected: startHeat }, "Mapa de calor día × hora"));
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
    return rhythm;
}
/** Tabla de identificadores por actividad, con la unión manual de alias. */
function authorsCard(s, au, refresh) {
    const c = s.concentration;
    const mergeControl = (a) => {
        const sel = h("select", { "aria-label": "Unir con" }, h("option", { value: "" }, "Unir con…"), au.authors.filter((b) => b.id !== a.id).map((b) => h("option", { value: b.id }, b.label)));
        sel.addEventListener("change", () => void (async () => {
            const target = au.authors.find((b) => b.id === Number(sel.value));
            if (!sel.value || !target)
                return;
            if (!confirm(`¿Tratar «${a.label}» y «${target.label}» como la misma persona?\n\nSolo vos sabés si lo son: nombres parecidos no son prueba. Se puede deshacer.`)) {
                sel.value = "";
                return;
            }
            await mergeAuthor(a.id, target.id);
            toast("Alias unidos.");
            await refresh();
        })());
        return sel;
    };
    return h("section", { class: "card authors" }, h("h2", null, "Identificadores de autor por actividad", kind("fact", "hecho")), h("p", null, h("small", null, au.note)), c.half_rank ? h("p", null, `La mitad de los mensajes la ${c.half_rank === 1 ? "escribió el identificador más activo" : `escribieron los ${n(c.half_rank)} identificadores más activos`}, de ${n(c.curve.length)} que participaron.`) : null, table([{ t: "#", num: true }, { t: "Identificador" }, { t: "Mensajes", num: true }, { t: "% del total", num: true }, { t: "% acumulado", num: true }, { t: "Días activos", num: true }, { t: "Primer / último día" }, { t: "Reconciliar alias" }], au.authors.slice(0, 50).map((a, i) => [n(i + 1),
        h("span", null, h("a", { href: "#/explorar?" + qs({ author_id: a.id }) }, a.label), a.aliases.map((x) => h("div", null, h("small", null, "+ " + x.label + " "), h("button", { class: "ghost small", onclick: () => void unmergeAuthor(x.id).then(refresh) }, "separar")))),
        n(a.messages), pct(a.share), pct(c.curve[i]?.share), n(a.active_days), `${a.first_day} / ${a.last_day}`, mergeControl(a)])), h("small", null, `Se muestran hasta 50 de ${n(au.authors.length)}. «% acumulado» suma esa fila y todas las anteriores. Denominador: ${n(au.denominator)} mensajes de participantes.`));
}
function domainsAndRecordsCards(s) {
    const t = s.totals;
    const dmax = Math.max(1, ...s.domains.map((d) => d.messages));
    return h("section", { class: "two" }, h("div", { class: "card" }, h("h3", null, "Dominios compartidos", kind("fact", "hecho")), s.domains.length ? hbars(s.domains.slice(0, 15).map((d) => ({
        label: h("a", { href: "#/explorar?" + qs({ q: d.domain, has_link: 1 }) }, d.domain), value: d.messages,
        right: `${n(d.messages)} msj · ${n(d.links)} enlaces`
    })), dmax, n) : h("p", { class: "muted" }, "Sin enlaces en el período."), h("small", null, "«msj» = mensajes distintos que mencionan el dominio; «enlaces» = apariciones.")), h("div", { class: "card" }, h("h3", null, "Registros que no son texto de participantes", kind("fact", "hecho")), h("div", { class: "kv" }, Object.entries(s.system_by_subtype).map(([k, v]) => [h("span", null, "Aviso: " + (SUBTYPE[k] ?? k)), h("span", null, n(v))]), Object.entries(s.media_by_kind).map(([k, v]) => [h("span", null, "Multimedia omitida: " + k), h("span", null, n(v))]), h("span", null, "Mensajes eliminados"), h("span", null, n(t.deleted_messages)), h("span", null, "No reconocidos"), h("span", null, n(t.unrecognized)))));
}
/** Lo que no es un hecho calculado: la clasificación automática vigente y lo que espera revisión. */
function categoriesAndReviewCards(s, cats) {
    const t = s.totals;
    const run = cats.run;
    return h("section", { class: "two" }, h("div", { class: "card" }, h("h3", null, "Categorías", kind("auto", "clasificación automática")), run ? h("p", null, h("small", null, `Proveedor: ${PROVIDER[run.provider]} (${run.model}). ${n(cats.classified_messages)} de ${n(cats.participant_messages)} mensajes clasificados. Estimaciones, no conteos oficiales.`))
        : h("p", { class: "muted" }, "Todavía no se ejecutó ninguna clasificación."), run ? hbars(categories().map((k) => ({
        label: h("a", { href: "#/explorar?" + qs({ category: k.key }) }, k.label), value: cats.effective[k.key]
    })), Math.max(1, ...Object.values(cats.effective)), n) : null, h("p", null, h("a", { href: "#/categorias" }, run ? "Ver y revisar categorías →" : "Ir a clasificar →"))), h("div", { class: "card" }, h("h3", null, "Para revisar", kind("pending", "interpretación pendiente")), h("div", { class: "kv" }, h("span", null, "Registros marcados para revisión"), h("span", null, n(t.flagged_for_review)), h("span", null, "Registros no reconocidos"), h("span", null, n(t.unrecognized)), h("span", null, "Clasificaciones corregidas a mano"), h("span", null, n(cats.corrected_messages))), t.flagged_for_review ? h("p", null, h("a", { href: "#/explorar?" + qs({ review_only: 1, record_type: "all" }) }, "Ver registros marcados →")) : null, h("p", null, h("small", null, "Las cifras describen actividad visible en el grupo. No permiten inferir intenciones ni quién originó una idea, y «sin seguimiento visible» no significa que no hubo seguimiento por privado."))));
}
