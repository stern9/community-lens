// Periódico: elegir un rango, ver qué enlaces entran y qué saldría de la máquina, y generar la edición.
// La vista previa no descarga páginas ni consulta modelos; eso solo pasa al apretar "Generar periódico".
import * as api from "../api.js";
import { h, kind, put, type Child } from "../dom.js";
import { errMsg, monthName, n, pad2, utcMinute } from "../format.js";
import { emptyState, view, type View } from "../shell.js";
import { state } from "../state.js";
import type { NewspaperEdition, NewspaperLink, NewspaperPreview, NewspaperResult } from "../types.js";

const PRESETS = [{ key: "week", label: "Última semana", days: 6 }, { key: "fortnight", label: "Últimas 2 semanas", days: 13 }, { key: "month", label: "Último mes", days: 29 }] as const;
const LINK_KIND: Readonly<Record<NewspaperLink["kind"], string>> = { article: "Artículos", social: "Redes sociales", tool: "Sitios y herramientas" };
const LINK_KINDS = ["article", "social", "tool"] as const;

export const viewPeriodico: View = async () => {
  if (state.chatId == null) return emptyState();
  const chatId = state.chatId;
  // Lo que falte del rango lo completa el backend con la misma regla que la línea de comandos: la vista previa
  // devuelve el rango resuelto y el último día con mensajes, y con eso se llenan los campos y los atajos.
  let lastDay = "";
  const from = h("input", { type: "date", value: state.from, "aria-label": "Desde" });
  const to = h("input", { type: "date", value: state.to, "aria-label": "Hasta" });
  const maxSel = h("select", null, [5, 8, 12, 15].map((x) => h("option", { value: x, selected: x === 8 }, String(x))));
  const orderSel = h("select", null, h("option", { value: "recent" }, "Los más recientes"), h("option", { value: "shared" }, "Los más compartidos"));
  const presets = h("div", { class: "presets" });
  const found = h("div"), models = h("div"), result = h("div"), saved = h("div");
  const go = h("button", { type: "button", disabled: true }, "Generar periódico");
  // Lo que se está por generar: la vista previa del rango y los modelos elegidos sobre ella.
  let current: NewspaperPreview | null = null;
  let picker: ModelPicker | null = null;
  const order = (): "recent" | "shared" => (orderSel.value === "shared" ? "shared" : "recent");

  const markPreset = (): void => {
    presets.querySelectorAll("button").forEach((b) => b.classList.toggle("on",
      lastDay !== "" && to.value === lastDay && from.value === daysBefore(lastDay, Number(b.dataset["days"]))));
  };
  PRESETS.forEach((p) => presets.append(h("button", { type: "button", class: "ghost", "data-days": p.days,
    onclick: () => { if (!lastDay) return; from.value = daysBefore(lastDay, p.days); to.value = lastDay; void refresh(); } }, p.label)));

  /** Solo se puede generar con artículos, con algún modelo y, si hay lecturas marcadas, con la confirmación. */
  const syncGo = (): void => {
    const choice = picker?.choice();
    go.disabled = !current || !choice || !current.links.article.length || (choice.readers.length > 0 && !choice.consent);
  };

  async function refresh(): Promise<void> {
    current = null; syncGo();
    if (from.value && to.value && from.value > to.value) { markPreset(); put(found, h("p", { class: "error" }, "Elegí un rango de fechas válido.")); return; }
    let p: NewspaperPreview;
    try { p = await api.previewNewspaper(chatId, from.value, to.value, order(), Number(maxSel.value)); }
    catch (e) { put(found, h("p", { class: "error" }, errMsg(e))); return; }
    from.value = p.from; to.value = p.to; lastDay = p.last_day ?? "";
    markPreset();
    current = p;
    picker = modelPicker(p, syncGo);
    put(found, linksFound(p));
    put(models, picker.nodes);
    syncGo();
  }

  // Las descartadas se ocultan salvo que se pidan; mostrarlas u ocultarlas redibuja sin volver a consultar.
  let editions: NewspaperEdition[] = [];
  let showDiscarded = false;
  const drawSaved = (): void => {
    const discarded = editions.filter((e) => e.status === "descartada").length;
    const shown = showDiscarded ? editions : editions.filter((e) => e.status !== "descartada");
    put(saved,
      shown.length ? h("ul", { class: "editions" }, shown.map((e) => editionRow(e, loadSaved)))
        : h("p", { class: "muted" }, editions.length ? "Todas las ediciones de este grupo están descartadas." : "Todavía no hay ediciones de este grupo."),
      discarded ? h("p", null, h("button", { type: "button", class: "ghost", onclick: () => { showDiscarded = !showDiscarded; drawSaved(); } },
        showDiscarded ? "Ocultar descartadas" : `Mostrar ${n(discarded)} descartada${discarded === 1 ? "" : "s"}`)) : null);
  };
  async function loadSaved(): Promise<void> {
    editions = await api.getEditions(chatId);
    drawSaved();
  }

  go.addEventListener("click", () => void (async () => {
    const choice = picker?.choice();
    if (!current || !choice) return;
    go.disabled = true;
    put(result, h("p", { class: "muted" }, "Generando… se descargan las páginas y el modelo redacta. Puede tardar de uno a tres minutos; no cierres esta pestaña."));
    try {
      const r = await api.generateNewspaper({
        chat_id: chatId, from: from.value, to: to.value, editor: choice.editor, readers: choice.readers,
        consent_chat: choice.consent, order: order(), max_articles: Number(maxSel.value),
      });
      put(result, editionReady(r));
      await loadSaved();
    } catch (e) {
      put(result, h("p", { class: "error" }, "No se pudo generar: " + errMsg(e)));
    }
    syncGo();
  })());
  [from, to, maxSel, orderSel].forEach((el) => el.addEventListener("change", () => void refresh()));

  put(view,
    h("section", { class: "card" },
      h("h2", null, "Periódico de la comunidad", kind("auto", "redacción automática")),
      h("p", null, h("small", null, "Arma una página para ponerse al día: los enlaces compartidos en un rango de fechas, resumidos por el modelo que elijás. Los enlaces, las fechas y las cifras salen del chat importado; los textos los escribe el modelo.")),
      h("h3", null, "1. Rango de fechas"),
      presets,
      h("div", { class: "row end" }, h("label", { class: "field" }, "Desde", from), h("label", { class: "field" }, "Hasta", to),
        h("label", { class: "field" }, "Artículos (máximo)", maxSel), h("label", { class: "field" }, "Prioridad", orderSel)),
      h("p", null, h("small", null, "Este rango es propio del periódico; no cambia el filtro de fechas de arriba.")),
      h("h3", null, "2. Qué entra"), found,
      h("h3", null, "3. Modelo"), models,
      h("div", { class: "actions" }, go), result),
    h("section", { class: "card" }, h("h2", null, "Ediciones guardadas"), saved));
  await Promise.all([refresh(), loadSaved()]);
};

// ------------------------------------------------------------------ fechas --
const isoDay = (d: Date): string => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
/** Día `days` antes de `day` ("AAAA-MM-DD"), al mediodía para esquivar cambios de horario. */
function daysBefore(day: string, days: number): string {
  const d = new Date(day + "T12:00:00");
  d.setDate(d.getDate() - days);
  return isoDay(d);
}

// ---------------------------------------------------------------- secciones --
/** Paso 2: qué enlaces del rango entran a la edición, por tipo. */
function linksFound(p: NewspaperPreview): Child[] {
  const f = p.facts;
  return [
    h("p", null, kind("fact", "hecho"), ` ${n(f.messages)} mensajes de ${n(f.participants)} participantes en el rango; ${n(f.messages_with_links)} con enlaces.`),
    p.links.article.length ? null : h("p", { class: "error" }, "No hay artículos compartidos en ese rango: no se puede armar una edición."),
    LINK_KINDS.map((k) => h("details", null,
      h("summary", null, `${LINK_KIND[k]}: ${n(p.links[k].length)} entran` + (p.links_total[k] > p.links[k].length ? ` de ${n(p.links_total[k])} compartidos` : "")),
      h("ol", { class: "linklist" }, p.links[k].map((l) => h("li", null, `${l.shared} · `, h("a", { href: l.url, target: "_blank", rel: "noopener noreferrer" }, l.url)))))),
    p.links_skipped ? h("p", null, h("small", null, `${n(p.links_skipped)} enlaces se omiten por no ser contenido (invitaciones, llamadas, formularios, archivos).`)) : null];
}

interface ModelChoice {
  /** Proveedor que redacta, o "auto". */
  editor: string;
  /** Proveedores a los que se les pide una lectura de la conversación. */
  readers: string[];
  /** La persona confirmó que quiere enviar mensajes seudonimizados. */
  consent: boolean;
}
interface ModelPicker {
  nodes: Child[];
  /** Lo elegido en este momento; `null` si no hay ningún modelo configurado. */
  choice: () => ModelChoice | null;
}

/** Paso 3: quién redacta, qué sale de la máquina y las lecturas opcionales con su confirmación. */
function modelPicker(p: NewspaperPreview, onChange: () => void): ModelPicker {
  const missing = p.missing_model.join(" y ");
  if (!p.providers.length) {
    return { choice: () => null, nodes: [
      h("p", { class: "error" }, "No hay ningún modelo configurado."),
      h("p", null, h("small", null, "Agregá una key en el archivo .env (ANTHROPIC_API_KEY, u OPENAI_API_KEY + OPENAI_MODEL, o GEMINI_API_KEY + GEMINI_MODEL) y reiniciá la app. La key se queda en tu máquina; nunca llega al navegador.")),
      missing ? h("p", { class: "error" }, `Hay key de ${missing} pero falta indicar el modelo en .env.`) : null] };
  }
  const name = (x: { label: string; model: string }): string => `${x.label} (${x.model})`;
  const several = p.providers.length > 1;
  const editor = h("select", null,
    several ? h("option", { value: "auto" }, `Automático: ${p.providers[0]?.label ?? ""}, y si falla el siguiente`) : null,
    p.providers.map((x) => h("option", { value: x.name }, name(x))));
  const consent = h("input", { type: "checkbox", onchange: onChange });
  const readers = p.providers.map((x) => ({ box: h("input", { type: "checkbox", value: x.name, onchange: onChange }), label: `La visión de ${name(x)}` }));
  const pages = p.links.article.length + p.links.social.length + p.links.tool.length;
  return {
    choice: () => ({ editor: editor.value, readers: readers.filter((r) => r.box.checked).map((r) => r.box.value), consent: consent.checked }),
    nodes: [
      h("label", { class: "field" }, "Modelo que redacta los resúmenes", editor),
      h("div", { class: "sendbox" }, h("b", null, "Qué sale de tu máquina al generar"),
        h("ul", null,
          h("li", null, `Siempre: el texto público de hasta ${n(pages)} páginas enlazadas va al modelo que redacta${several ? " (en automático, también al siguiente si el primero falla)" : ""}. Las páginas se descargan desde esta máquina.`),
          h("li", null, "Nunca: nombres, teléfonos ni quién compartió cada enlace."),
          h("li", null, `Solo si activás una lectura: ${n(p.transcript.messages)} mensajes del período (${n(p.transcript.characters)} caracteres) con seudónimos en lugar de nombres, sin teléfonos, correos ni menciones. Un nombre escrito dentro de un mensaje sí podría ir.`))),
      h("p", null, h("b", null, "Lecturas de la conversación"), " ", kind("auto", "interpretación del modelo"), h("br"),
        h("small", null, "Opcional. Cada modelo elegido escribe dos párrafos sobre de qué se habló. Los conteos por tema los hace el código.")),
      readers.map((r) => h("label", { class: "row" }, r.box, r.label)),
      h("label", { class: "row" }, consent, "Confirmo que quiero enviar esos mensajes seudonimizados a los modelos marcados."),
      missing ? h("p", { class: "muted" }, `Hay key de ${missing} sin modelo indicado en .env; no se puede usar todavía.`) : null],
  };
}

/** Resultado de generar: qué quedó en la edición, avisos y cómo abrirla. */
function editionReady(r: NewspaperResult): Child[] {
  return [
    h("p", null, h("b", null, "Edición lista. "), `${n(r.articles)} artículos, ${n(r.socials)} publicaciones en redes, ${n(r.tools)} sitios` +
      (r.unread ? `, ${n(r.unread)} que no se pudieron leer` : "") + (r.readings ? ` y ${n(r.readings)} lectura${r.readings === 1 ? "" : "s"} de la conversación.` : ".")),
    r.warnings.map((w) => h("p", { class: "error" }, w)),
    h("p", null, h("small", null, "Revisala antes de compartirla: los resúmenes son automáticos y pueden tener errores.")),
    r.status === "borrador" ? h("p", null, "Quedó como borrador: aprobala en Ediciones guardadas para que entre en el sitio.") : null,
    h("div", { class: "actions" },
      h("a", { href: api.editionUrl(r.id), target: "_blank", rel: "noopener", class: "btnlink" }, "Abrir el periódico"),
      h("a", { href: api.editionUrl(r.id, false, false, true), target: "_blank", rel: "noopener", class: "btnlink" }, "Versión pública"),
      h("a", { href: api.editionUrl(r.id, true), class: "btnlink" }, "Descargar HTML"))];
}

/** "21 oct" de un instante UTC "AAAA-MM-DDTHH:MM:SSZ". */
const shortDay = (iso: string): string => `${Number(iso.slice(8, 10))} ${monthName(iso.slice(0, 7))}`;

/** El estado de una edición guardada: si está en el sitio, quién la aprobó y qué hay que mirar. */
function editionState(e: NewspaperEdition): string {
  const a = e.approval;
  switch (e.status) {
    case "borrador": return "borrador";
    case "descartada": return "descartada";
    case "heredada": return "heredada, sin registro de aprobación" + (e.in_site ? "" : " · reemplazada por una más nueva");
    case "aprobada": {
      const who = a && a.by ? `aprobada por ${a.by} (${a.via}, ${shortDay(a.at)})` : "aprobada";
      if (e.blocked_reason) return `${who} · ${e.blocked_reason}`;
      if (!e.in_site) return `${who} · reemplazada por una más nueva`;
      return `${who} · en el sitio` + (e.up_to_date === false ? " · hay plantilla nueva: reaprobá para actualizarla" : "");
    }
  }
}

/** Una edición guardada: su rango, quién la redactó, qué se envió para hacerla, su estado y qué se puede hacer con
 * ella. Cada acción llama a la API y, si sale bien, `reload` vuelve a pedir la lista; si falla, el error queda en la fila. */
function editionRow(e: NewspaperEdition, reload: () => Promise<void>): HTMLLIElement {
  const form = h("span"), msg = h("span", { class: "error" });
  const act = (call: () => Promise<unknown>) => (): void => void (async () => {
    try { await call(); await reload(); } catch (err) { put(msg, " " + errMsg(err)); }
  })();
  const button = (label: string, onclick: () => void): HTMLButtonElement => h("button", { type: "button", class: "ghost", onclick }, label);
  // Aprobar pide quién y por qué canal en la misma fila; queda registrado junto con los bytes que se publican.
  const askApproval = (): void => {
    const by = h("input", { type: "text", maxlength: 200, placeholder: "Quién aprueba", "aria-label": "Quién aprueba" });
    const via = h("input", { type: "text", maxlength: 200, placeholder: "Canal (WhatsApp, correo…)", "aria-label": "Canal por el que se aprobó" });
    put(form, " ", by, " ", via, " ", h("button", { type: "button", onclick: act(() => api.approveEdition(e.id, { by: by.value, via: via.value })) }, "Confirmar"));
  };
  // Descartar una aprobada que está en el sitio la saca en la próxima publicación, y recuperarla no devuelve los bytes
  // aprobados: vuelve como borrador y hay que aprobarla de nuevo. Por eso se pregunta antes.
  const discard = act(() => api.discardEdition(e.id));
  const askDiscard = (): void => {
    if (confirm("Esta edición está aprobada y en el sitio. Si la descartás, sale del sitio en la próxima publicación; si después "
      + "la recuperás, vuelve como borrador y hay que aprobarla de nuevo. ¿Descartarla?")) discard();
  };
  const actions = e.status === "descartada" ? [button("Recuperar", act(() => api.restoreEdition(e.id)))]
    : [button(e.status === "aprobada" ? "Aprobar de nuevo…" : "Aprobar…", askApproval), " ",
      button("Descartar", e.status === "aprobada" && e.in_site ? askDiscard : discard)];
  return h("li", null,
    `${e.from} a ${e.to} · generada ${utcMinute(e.created_at)} UTC · ${e.editor.label}${e.sent.pages_tried.length ? ` (falló ${e.sent.pages_tried.join(" y ")})` : ""} · `,
    e.sent.chat_messages ? `${n(e.sent.chat_messages)} mensajes enviados a ${e.sent.chat_to.join(" y ")} · ` : "sin texto del chat enviado · ",
    h("b", null, editionState(e)), " · ",
    h("a", { href: api.editionUrl(e.id), target: "_blank", rel: "noopener", class: "btnlink" }, "Abrir"), " ",
    h("a", { href: api.editionUrl(e.id, false, false, true), target: "_blank", rel: "noopener", class: "btnlink" }, "Versión pública"), " ",
    h("a", { href: api.editionUrl(e.id, true), class: "btnlink" }, "Descargar HTML"), " ",
    actions, form, msg);
}
