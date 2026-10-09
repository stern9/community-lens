// Explorar: búsqueda de texto completo con filtros. Los filtros viven en la URL, así se pueden compartir y volver atrás.
import * as api from "../api.js";
import { qs } from "../api.js";
import { messageCard } from "../components/message.js";
import { h, put } from "../dom.js";
import { n } from "../format.js";
import { view, type View } from "../shell.js";
import { categories, scope, state } from "../state.js";

export const viewExplorar: View = async ({ params }) => {
  const f = readFilters(params);
  if (state.chatId != null && !state.authors.length) state.authors = (await api.getAuthors({ chat_id: state.chatId, from: "", to: "" })).authors;
  const select = (label: string, current: string, options: readonly (readonly [string, string])[]): HTMLSelectElement =>
    h("select", { "aria-label": label }, options.map(([v, t]) => h("option", { value: v, selected: current === v }, t)));
  const check = (checked: boolean): HTMLInputElement => h("input", { type: "checkbox", checked });
  const q = h("input", { value: f.q, type: "search", placeholder: "Buscar texto… (usá \"comillas\" para frases, term* para prefijos)", "aria-label": "Buscar" });
  const author = select("Autor", f.author_id, [["", "Todos"], ...state.authors.map((a) => [String(a.id), `${a.label} (${n(a.messages)})`] as const)]);
  const category = select("Categoría", f.category, [["", "Todas"], ...categories().map((c) => [c.key, c.label] as const)]);
  const rtype = select("Tipo de registro", f.record_type, [["message", "Mensajes de participantes"], ["system", "Avisos del sistema"], ["unrecognized", "No reconocidos"], ["all", "Todos los registros"]]);
  const order = select("Orden", f.order, [["recent", "Más recientes"], ["oldest", "Más antiguos"], ["relevance", "Relevancia"]]);
  const cLink = check(f.has_link), cMedia = check(f.has_media), cRev = check(f.review_only), cHum = check(f.human_only), cAll = check(f.all_chats);
  const results = h("div"), status = h("p", { class: "muted" });
  const apply = (e?: Event): void => {
    e?.preventDefault();
    location.hash = "#/explorar?" + qs({
      q: q.value.trim(), author_id: author.value, category: category.value,
      record_type: rtype.value === "message" ? "" : rtype.value, order: order.value === "recent" ? "" : order.value,
      has_link: cLink.checked && 1, has_media: cMedia.checked && 1, review_only: cRev.checked && 1, human_only: cHum.checked && 1, all_chats: cAll.checked && 1,
    });
  };
  const form = h("form", { class: "card", onsubmit: apply },
    h("h2", null, "Explorar mensajes"),
    h("div", { class: "filters" },
      h("div", { class: "wide row" }, q, h("button", { type: "submit" }, "Buscar")),
      h("label", { class: "field" }, "Autor (identificador)", author), h("label", { class: "field" }, "Categoría", category),
      h("label", { class: "field" }, "Tipo de registro", rtype), h("label", { class: "field" }, "Orden", order)),
    h("div", { class: "checks" },
      h("label", { class: "row" }, cLink, "Con enlaces"), h("label", { class: "row" }, cMedia, "Con multimedia omitida"),
      h("label", { class: "row" }, cRev, "Marcados para revisión"), h("label", { class: "row" }, cHum, "Corregidos a mano"),
      h("label", { class: "row" }, cAll, "Buscar en todos los grupos")),
    h("small", null, "Grupo y fechas se eligen arriba. La categoría usa la corrección humana si existe; si no, la clasificación automática vigente."));
  q.style.flex = "1";
  q.style.minWidth = "180px";
  [author, category, rtype, order, cLink, cMedia, cRev, cHum, cAll].forEach((el) => el.addEventListener("change", apply));
  put(view, form, h("section", null, status, results));

  let offset = 0;
  async function load(): Promise<void> {
    const r = await api.search({
      ...(f.all_chats ? { from: state.from, to: state.to } : scope()),
      q: f.q, author_id: f.all_chats ? "" : f.author_id, category: f.category, record_type: f.record_type,
      has_link: f.has_link, has_media: f.has_media, review_only: f.review_only, human_only: f.human_only, order: f.order, limit: 40, offset,
    });
    status.textContent = `${n(r.total)} resultado${r.total === 1 ? "" : "s"}` + (f.q ? ` para «${f.q}»` : "") + (f.all_chats ? " en todos los grupos" : "");
    results.querySelector(".more")?.remove();
    const reload = (): Promise<void> => { offset = 0; put(results); return load(); };
    r.items.forEach((m) => results.append(messageCard(m, { onChange: reload })));
    offset += r.items.length;
    if (offset < r.total) results.append(h("p", { class: "more" }, h("button", { class: "ghost", onclick: () => void load() }, `Cargar más (${n(r.total - offset)} restantes)`)));
    if (!r.total) results.append(h("p", { class: "muted" }, "Sin resultados con estos filtros."));
  }
  await load();
};

interface ExploreFilters {
  q: string;
  author_id: string;
  category: string;
  record_type: string;
  order: string;
  has_link: boolean;
  has_media: boolean;
  review_only: boolean;
  human_only: boolean;
  all_chats: boolean;
}
function readFilters(params: URLSearchParams): ExploreFilters {
  const text = (k: string, fallback = ""): string => params.get(k) ?? fallback;
  const flag = (k: string): boolean => Boolean(params.get(k));
  return {
    q: text("q"), author_id: text("author_id"), category: text("category"), record_type: text("record_type", "message"), order: text("order", "recent"),
    has_link: flag("has_link"), has_media: flag("has_media"), review_only: flag("review_only"), human_only: flag("human_only"), all_chats: flag("all_chats"),
  };
}
