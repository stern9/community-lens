// Formato de números, fechas y etiquetas. Sin acceso al DOM.
export const WEEKDAYS = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"];
export const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
/** Índices de día (0 = domingo) en orden de lectura: lunes primero. */
export const DAY_ORDER = [1, 2, 3, 4, 5, 6, 0];
/** Nombre corto del día; índice 0 = domingo. */
export const weekdayName = (i) => WEEKDAYS[i] ?? "?";
/** Nombre corto del mes a partir de "AAAA-MM". */
export const monthName = (ym) => MONTHS[Number(ym.slice(5, 7)) - 1] ?? "?";
/** "2026-03" → "mar-26". */
export const monthLabel = (ym) => `${monthName(ym)}-${ym.slice(2, 4)}`;
const nf = new Intl.NumberFormat("es-CR");
/** Entero con separador de miles; "—" si no hay dato. */
export const n = (x) => (x == null ? "—" : nf.format(x));
/** Decimal con `d` cifras; "—" si no hay dato. */
export const dec = (x, d = 1) => x == null ? "—" : new Intl.NumberFormat("es-CR", { minimumFractionDigits: d, maximumFractionDigits: d }).format(x);
/** Proporción 0–1 como porcentaje con una cifra decimal. */
export const pct = (x) => (x == null ? "—" : (x * 100).toFixed(1).replace(".", ",") + " %");
/** "09" para 9. */
export const pad2 = (x) => String(x).padStart(2, "0");
/** Mensaje legible de cualquier valor capturado en un catch. */
export const errMsg = (e) => (e instanceof Error ? e.message : String(e));
