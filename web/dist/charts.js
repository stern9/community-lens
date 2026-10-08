import { DAY_ORDER, monthLabel, monthName, n, pad2, pct, weekdayName } from "./format.js";
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const plotWidth = () => Math.max(340, Math.min(1060, window.innerWidth - 64));
const heatColor = (label) => ({ type: "sqrt", range: [cssVar("--heat0"), cssVar("--heat1")], legend: true, label });
const dayDomain = () => DAY_ORDER.map(weekdayName);
/** Día calendario local a partir de "AAAA-MM-DD", al mediodía para esquivar saltos de horario. */
const noon = (day) => new Date(day + "T12:00:00");
const isoDay = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
/** Completa con ceros los días sin mensajes entre el primero y el último. */
export function fillDays(rows) {
    const first = rows[0], last = rows[rows.length - 1];
    if (!first || !last)
        return [];
    const byDay = new Map(rows.map((r) => [r.day, r]));
    const out = [];
    const end = noon(last.day);
    for (const d = noon(first.day); d <= end; d.setDate(d.getDate() + 1)) {
        const k = isoDay(d);
        out.push(byDay.get(k) ?? { day: k, messages: 0, authors: 0 });
    }
    return out;
}
export function monthChart(monthly) {
    const rows = monthly.map((r) => ({
        label: monthLabel(r.month) + (r.partial ? "*" : ""),
        messages: r.messages,
        partial: r.partial,
        tip: `${monthLabel(r.month)}: ${n(r.messages)} mensajes · ${n(r.authors)} identificadores activos` +
            (r.partial ? ` · mes parcial (${r.days_covered} de ${r.days_in_month} días)` : ""),
    }));
    return Plot.plot({
        className: "clplot", width: plotWidth(), height: 260, marginTop: 24,
        x: { domain: rows.map((r) => r.label), label: null, padding: 0.12 },
        y: { grid: true, label: "mensajes", tickFormat: (v) => n(v) },
        marks: [
            Plot.barY(rows, { x: "label", y: "messages", fill: cssVar("--series"), fillOpacity: (r) => (r.partial ? 0.4 : 1), rx: 4, title: "tip", tip: true }),
            rows.length <= 14 ? Plot.text(rows, { x: "label", y: "messages", text: (r) => n(r.messages), dy: -8, fontSize: 11 }) : null,
            Plot.ruleY([0]),
        ],
    });
}
/** Una celda por día: columnas = semanas, filas = día de la semana. */
export function calendarChart(daily) {
    const filled = fillDays(daily);
    const first = filled[0];
    if (!first)
        return Plot.plot({ className: "clplot", width: plotWidth(), height: 40, marks: [] });
    const utcNoon = (day) => new Date(day + "T12:00:00Z");
    const origin = d3.utcMonday(utcNoon(first.day));
    const days = filled.map((r) => {
        const date = utcNoon(r.day);
        return { ...r, week: d3.utcMonday.count(origin, date), wd: weekdayName(date.getUTCDay()) };
    });
    const firstOfMonth = new Map();
    days.forEach((d, i) => { if (i === 0 || d.day.endsWith("-01"))
        firstOfMonth.set(d.week, monthLabel(d.day.slice(0, 7))); });
    const lastWeek = days[days.length - 1]?.week ?? 0;
    return Plot.plot({
        className: "clplot", width: plotWidth(), height: 220, marginLeft: 40, padding: 0,
        x: { domain: d3.range(0, lastWeek + 1), axis: "top", label: null, ticks: [...firstOfMonth.keys()], tickFormat: (w) => firstOfMonth.get(w) ?? "", tickSize: 0 },
        y: { domain: dayDomain(), label: null, tickSize: 0 },
        color: heatColor("mensajes por día"),
        marks: [Plot.cell(days, { x: "week", y: "wd", fill: "messages", inset: 1, rx: 2, tip: true,
                title: (d) => `${d.day} (${d.wd}): ${n(d.messages)} mensajes · ${n(d.authors)} identificadores` })],
    });
}
export function simpleBars(rows, opts = {}) {
    const { height = 200, valueLabels = false } = opts;
    return Plot.plot({
        className: "clplot", width: 500, height, marginTop: 22, marginBottom: 26,
        x: { domain: rows.map((r) => r.label), label: null, padding: 0.12, tickSize: 0 },
        y: { grid: true, label: null, ticks: 4, tickFormat: (v) => n(v) },
        marks: [
            Plot.barY(rows, { x: "label", y: "value", fill: cssVar("--series"), rx: 3, title: "tip", tip: true }),
            valueLabels ? Plot.text(rows, { x: "label", y: "value", text: (r) => n(r.value), dy: -8, fontSize: 11 }) : null,
            Plot.ruleY([0]),
        ],
    });
}
/** Clave de la celda día × hora. */
export const cellKey = (weekday, hour) => `${weekday}-${hour}`;
export const cellMap = (cells) => new Map(cells.map((d) => [cellKey(d.weekday, d.hour), d.messages]));
export function hourHeatmap(weekdayHour) {
    const byCell = cellMap(weekdayHour);
    const cells = DAY_ORDER.flatMap((wd) => d3.range(24).map((hour) => ({ wd: weekdayName(wd), hour, messages: byCell.get(cellKey(wd, hour)) ?? 0 })));
    return Plot.plot({
        className: "clplot", width: plotWidth(), height: 260, marginLeft: 40, marginBottom: 40, padding: 0,
        x: { domain: d3.range(24), label: "hora del día", labelAnchor: "center", labelOffset: 34, tickSize: 0 },
        y: { domain: dayDomain(), label: null, tickSize: 0 },
        color: heatColor("mensajes"),
        marks: [Plot.cell(cells, { x: "hour", y: "wd", fill: "messages", inset: 1.5, rx: 3, tip: true,
                title: (d) => `${d.wd} ${pad2(d.hour)}:00–${pad2(d.hour)}:59 · ${n(d.messages)} mensajes` })],
    });
}
/** Porcentaje de los mensajes clasificados de cada mes que tienen la categoría `key`. */
export function categoryMiniChart(months, key) {
    const multiYear = new Set(months.map((r) => r.month.slice(0, 4))).size > 1;
    const rows = months.map((r) => ({
        label: (multiYear ? monthLabel(r.month) : monthName(r.month)) + (r.partial ? "*" : ""),
        share: r.shares[key] ?? 0,
        partial: r.partial,
        tip: `${monthLabel(r.month)}: ${n(r.counts[key])} de ${n(r.classified)} mensajes clasificados (${pct(r.shares[key])})` + (r.partial ? " · mes parcial" : ""),
    }));
    return Plot.plot({
        className: "clplot", width: 360, height: 150, marginLeft: 44, marginTop: 10, marginBottom: 26,
        x: { domain: rows.map((r) => r.label), label: null, padding: 0.15, tickSize: 0 },
        y: { label: null, grid: true, ticks: 3, tickFormat: (v) => (v * 100).toFixed(v < 0.1 ? 1 : 0).replace(".", ",") + " %" },
        marks: [
            Plot.barY(rows, { x: "label", y: "share", fill: cssVar("--series"), fillOpacity: (r) => (r.partial ? 0.4 : 1), rx: 3, title: "tip", tip: true }),
            Plot.ruleY([0]),
        ],
    });
}
