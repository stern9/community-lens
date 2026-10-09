// Formato de números, fechas y etiquetas. Sin acceso al DOM.

export const WEEKDAYS = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"] as const;
export const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"] as const;
/** Índices de día (0 = domingo) en orden de lectura: lunes primero. */
export const DAY_ORDER = [1, 2, 3, 4, 5, 6, 0] as const;

/** Nombre corto del día; índice 0 = domingo. */
export const weekdayName = (i: number): string => WEEKDAYS[i] ?? "?";
/** Nombre corto del mes a partir de "AAAA-MM". */
export const monthName = (ym: string): string => MONTHS[Number(ym.slice(5, 7)) - 1] ?? "?";
/** "2026-03" → "mar-26". */
export const monthLabel = (ym: string): string => `${monthName(ym)}-${ym.slice(2, 4)}`;

const nf = new Intl.NumberFormat("es-CR");
/** Entero con separador de miles; "—" si no hay dato. */
export const n = (x: number | null | undefined): string => (x == null ? "—" : nf.format(x));
/** Decimal con `d` cifras; "—" si no hay dato. */
export const dec = (x: number | null | undefined, d = 1): string =>
  x == null ? "—" : new Intl.NumberFormat("es-CR", { minimumFractionDigits: d, maximumFractionDigits: d }).format(x);
/** Proporción 0–1 como porcentaje con una cifra decimal. */
export const pct = (x: number | null | undefined): string => (x == null ? "—" : (x * 100).toFixed(1).replace(".", ",") + " %");
/** "09" para 9. */
export const pad2 = (x: number): string => String(x).padStart(2, "0");
/** Mensaje legible de cualquier valor capturado en un catch. */
export const errMsg = (e: unknown): string => (e instanceof Error ? e.message : String(e));

/** "2026-10-08T14:03:22" -> "2026-10-08T14:03" (hora local del chat, tal como viene). */
export const minute = (ts: string): string => ts.slice(0, 16);
/** "2026-10-08T14:03:22Z" -> "2026-10-08 14:03" (UTC). */
export const utcMinute = (iso: string): string => iso.replace("T", " ").slice(0, 16);
