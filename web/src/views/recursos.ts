// Recursos: documentos (agrupados por versión) y enlaces compartidos en el grupo.
import * as api from "../api.js";
import { qs } from "../api.js";
import { h, kind, put, table } from "../dom.js";
import { minute, n } from "../format.js";
import { emptyState, view, type View } from "../shell.js";
import { scope, state } from "../state.js";
import type { DocumentResource, LinkResource } from "../types.js";

export const viewRecursos: View = async ({ params }) => {
  if (state.chatId == null) return emptyState();
  const kindParam = params.get("tipo") === "enlaces" ? "link" : "document";
  const orderParam = params.get("orden") === "compartidos" ? "shared" : "recent";
  const q = h("input", { value: params.get("q") ?? "", type: "search", placeholder: "Nombre del archivo, dominio o quién lo compartió…", "aria-label": "Buscar recurso" });
  const kindSel = h("select", { "aria-label": "Tipo" },
    h("option", { value: "document", selected: kindParam === "document" }, "Documentos"), h("option", { value: "link", selected: kindParam === "link" }, "Enlaces"));
  const orderSel = h("select", { "aria-label": "Orden" },
    h("option", { value: "recent", selected: orderParam === "recent" }, "Más recientes"), h("option", { value: "shared", selected: orderParam === "shared" }, "Más compartidos"));
  const apply = (e?: Event): void => {
    e?.preventDefault();
    location.hash = "#/recursos?" + qs({ q: q.value.trim(), tipo: kindSel.value === "link" ? "enlaces" : "", orden: orderSel.value === "shared" ? "compartidos" : "" });
  };
  kindSel.addEventListener("change", apply);
  orderSel.addEventListener("change", apply);
  q.style.flex = "1";
  q.style.minWidth = "180px";
  const status = h("p", { class: "muted" }), results = h("div"), note = h("small");
  put(view,
    h("form", { class: "card", onsubmit: apply },
      h("h2", null, "Recursos compartidos", kind("fact", "hecho")),
      h("p", null, h("small", null, "Documentos y enlaces que se compartieron en el grupo. Los documentos se agrupan por nombre, con la última versión arriba.")),
      h("div", { class: "row end" }, q, h("label", { class: "field" }, "Tipo", kindSel), h("label", { class: "field" }, "Orden", orderSel), h("button", { type: "submit" }, "Buscar")),
      note),
    h("section", null, status, results));

  let offset = 0;
  async function load(): Promise<void> {
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
    if (offset < r.total) results.append(h("p", { class: "more" }, h("button", { class: "ghost", onclick: () => void load() }, `Cargar más (${n(r.total - offset)} restantes)`)));
    if (!r.total) results.append(h("p", { class: "muted" }, "Sin resultados."));
  }
  await load();
};

const people = (names: readonly string[]): string =>
  names.length <= 3 ? names.join(", ") : `${names.slice(0, 3).join(", ")} y ${n(names.length - 3)} más`;

function documentCard(d: DocumentResource): HTMLElement {
  const v = d.latest;
  const versionTag = (version: string | null): string => (version ? `v${version}` : "sin número de versión");
  return h("article", { class: "msg" },
    h("div", { class: "meta" }, h("b", null, d.title),
      h("span", null, `${n(d.shares)} envío${d.shares === 1 ? "" : "s"}`), h("span", null, `por ${people(d.sharers) || "—"}`)),
    h("div", { class: "body" },
      d.versions.length > 1 ? h("span", { class: "chip human" }, "última versión") : null, " ",
      h("a", { href: `#/m/${v.message_id}` }, v.name), ` · ${versionTag(v.version)} · ${minute(v.ts_local)} · ${v.author ?? "—"}`),
    d.latest_is_most_recent ? null
      : h("div", { class: "notice" }, `Ojo: lo último que se envió (${minute(d.last_shared)}) fue una versión anterior a la ${versionTag(v.version)}.`),
    d.versions.length > 1 ? h("details", null, h("summary", null, `Todas las versiones (${d.versions.length})`),
      table([{ t: "Versión" }, { t: "Archivo" }, { t: "Último envío" }, { t: "Por" }, { t: "Veces", num: true }],
        d.versions.map((x) => [versionTag(x.version), h("a", { href: `#/m/${x.message_id}` }, x.name), minute(x.ts_local), x.author ?? "—", n(x.times_shared)]))) : null,
    h("div", { class: "actions" }, h("a", { href: `#/m/${v.message_id}`, class: "btnlink" }, "Ver en la conversación")));
}
function linkCard(l: LinkResource): HTMLElement {
  return h("article", { class: "msg" },
    h("div", { class: "meta" }, h("b", null, l.domain), h("span", null, `${n(l.shares)} mensaje${l.shares === 1 ? "" : "s"}`),
      h("span", null, `último: ${minute(l.last_shared)} · ${l.author ?? "—"}`)),
    // El enlace externo no se abre solo: rel="noopener noreferrer" y pestaña nueva.
    h("div", { class: "body" }, h("a", { href: l.url, target: "_blank", rel: "noopener noreferrer" }, l.url)),
    h("div", { class: "actions" }, h("a", { href: `#/m/${l.message_id}`, class: "btnlink" }, "Ver en la conversación")));
}
