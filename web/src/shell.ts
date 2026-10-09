// El marco fijo de la página (index.html): zona de contenido, panel lateral, barra de grupo y fechas, avisos.
import { byId, h, put } from "./dom.js";
import { state } from "./state.js";

export const view = byId("view", HTMLElement);
export const drawer = byId("drawer", HTMLElement);

/** Lo que recibe cada vista del enrutador. `refresh` vuelve a dibujar la pantalla actual con datos nuevos. */
export interface ViewContext {
  params: URLSearchParams;
  refresh: () => Promise<void>;
}
export type View = (ctx: ViewContext) => Promise<void>;

let toastTimer: number | undefined;
export function toast(msg: string): void {
  const t = byId("toast", HTMLElement);
  t.textContent = msg;
  t.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { t.hidden = true; }, 3200);
}

export function emptyState(): void {
  put(view, h("section", { class: "card" }, h("h2", null, "Todavía no hay grupos importados"),
    h("p", null, "Empezá importando una exportación TXT de WhatsApp."), h("a", { href: "#/importar", class: "btnlink" }, "Importar un chat")));
}

/** Refleja el estado en la barra superior y recuerda el grupo elegido para la próxima visita. */
export function syncScope(): void {
  const sel = byId("chat", HTMLSelectElement);
  put(sel, state.chats.map((c) => h("option", { value: c.id, selected: c.id === state.chatId }, c.name)));
  sel.disabled = !state.chats.length;
  byId("from", HTMLInputElement).value = state.from;
  byId("to", HTMLInputElement).value = state.to;
  try { localStorage.setItem("cl.chat", state.chatId == null ? "" : String(state.chatId)); } catch { /* almacenamiento no disponible */ }
}
