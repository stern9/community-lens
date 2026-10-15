// Prueba de la vista real con API y DOM mínimos de mentira: sin navegador, red ni dependencias nuevas.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { setImmediate } from "node:timers/promises";
import { SourceTextModule, SyntheticModule } from "node:vm";

const nodes = [];
function h(tag, attrs = {}, ...children) {
  const node = {
    tag, ...attrs, value: attrs?.value ?? "", children: children.flat(Infinity).filter(Boolean),
    handlers: {}, dataset: { days: attrs?.["data-days"] }, classList: { toggle() {} },
    append(...kids) { this.children.push(...kids); },
    addEventListener(event, handler) { this.handlers[event] = handler; },
    querySelectorAll(tag) { return this.children.filter((child) => child.tag === tag); },
  };
  if (tag === "select") node.value = node.children.find((child) => child.selected)?.value ?? node.children[0]?.value;
  for (const [key, value] of Object.entries(attrs ?? {})) {
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
  }
  nodes.push(node);
  return node;
}
const put = (node, ...children) => { node.children = children.flat(Infinity).filter(Boolean); };
const previews = [], generations = [], approvals = [], discards = [], confirms = [];
let editionLoads = 0;
// Una edición guardada por estado, con todos los campos de NewspaperEdition.
const approval = { hash: "sha256:" + "0".repeat(64), by: "Jaime", via: "WhatsApp", at: "2026-10-21T14:03:00Z", to: "sitio público" };
const edition = (id, status, extra = {}) => ({
  id, from: "2026-10-01", to: "2026-10-07", created_at: "2026-10-08T12:00:00Z", editor: { name: "demo", label: "Demo", model: "demo" },
  sent: { pages: 1, pages_to: "Demo", pages_tried: [], chat_messages: 0, chat_to: [] }, status,
  approval: null, hash_ok: null, up_to_date: null, in_site: false, ...extra });
const EDITIONS = [
  edition(5, "borrador"),
  edition(4, "aprobada", { approval, hash_ok: true, up_to_date: true, in_site: true }),
  edition(3, "aprobada", { approval, hash_ok: true, up_to_date: false, in_site: true }),
  edition(2, "heredada", { in_site: true }),
  edition(1, "descartada"),
];
const view = h("main");
const state = { chatId: 1, from: "", to: "" };
const api = {
  async previewNewspaper(chatId, from, to) {
    previews.push({ chatId, from, to });
    return {
      from: from || "2026-09-27", to: to || "2026-10-03", last_day: "2026-10-03",
      facts: { messages: 1, participants: 1, messages_with_links: 1 },
      links: { article: [{ url: "https://example.org/nota", shared: "2026-10-01" }], social: [], tool: [] },
      links_total: { article: 1, social: 0, tool: 0 }, links_skipped: 0,
      providers: [{ name: "demo", label: "Demo", model: "demo" }], missing_model: [],
      transcript: { messages: 1, characters: 10 },
    };
  },
  async getEditions() { editionLoads++; return EDITIONS; },
  editionUrl: (id, download = false) => `/periodico/${id}` + (download ? "?download=true" : ""),
  async discardEdition(id) { discards.push(id); return EDITIONS[0]; },
  async restoreEdition() { assert.fail("la prueba no recupera"); },
  async approveEdition(id, body) { approvals.push({ id, ...body }); return EDITIONS[0]; },
  async generateNewspaper(body) { generations.push(body); throw new Error("Fin de la prueba: no se consulta ningún proveedor"); },
};
const mocks = {
  "../api.js": api,
  "../dom.js": { h, put, kind: (cls, text) => h("span", { class: cls }, text) },
  "../shell.js": { view, emptyState() { assert.fail("la prueba tiene un grupo elegido"); } },
  "../state.js": { state },
};
const module = new SourceTextModule(await readFile(new URL("../../web/dist/views/periodico.js", import.meta.url), "utf8"));
await module.link(async (name) => {
  if (name === "../format.js") return new SourceTextModule(await readFile(new URL("../../web/dist/format.js", import.meta.url), "utf8"));
  const exports = mocks[name];
  assert.ok(exports, `Importación inesperada: ${name}`);
  return new SyntheticModule(Object.keys(exports), function () {
    for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
  });
});
await module.evaluate();
await module.namespace.viewPeriodico({ params: new URLSearchParams(), refresh: async () => {} });
const [from, to] = nodes.filter((node) => node.tag === "input" && node.type === "date");
const generate = nodes.find((node) => node.tag === "button" && node.children.includes("Generar periódico"));

// Borrar cualquiera de los extremos después de cargar debe consultar la vista previa y completar el rango.
for (const field of [from, to]) {
  const before = previews.length;
  field.value = "";
  field.handlers.change();
  await setImmediate();
  assert.equal(previews.length, before + 1);
  assert.equal(previews.at(-1)[field === from ? "from" : "to"], "");
  assert.equal(from.value, "2026-09-27");
  assert.equal(to.value, "2026-10-03");
  assert.equal(generate.disabled, false);
}
generate.handlers.click();
await setImmediate();
assert.equal(generations.length, 1);
assert.equal(generations[0].from, "2026-09-27");
assert.equal(generations[0].to, "2026-10-03");
assert.equal(state.from, "");
assert.equal(state.to, "");

// Un rango invertido deshabilita generar y se rechaza antes de pedir otra vista previa.
const before = previews.length;
from.value = "2026-10-04";
from.handlers.change();
await setImmediate();
assert.equal(previews.length, before);
assert.equal(generate.disabled, true);

// Ediciones guardadas: una fila por estado, con su texto; la descartada queda oculta hasta pedirla.
const text = (node) => (typeof node === "object" ? node.children.map(text).join("") : String(node));
const savedList = () => nodes.filter((node) => node.tag === "ul" && node.class === "editions").at(-1);
const rows = () => savedList().children.map(text);
assert.equal(rows().length, 4);
assert.match(rows()[0], /· borrador · Abrir/);
assert.match(rows()[1], /aprobada por Jaime \(WhatsApp, 21 oct\) · en el sitio · Abrir/);
assert.match(rows()[2], /· en el sitio · hay plantilla nueva: reaprobá para actualizarla/);
assert.match(rows()[3], /heredada, sin registro de aprobación · Abrir/);
assert.ok(rows().every((row) => !row.includes("descartada")));
const button = (label) => nodes.filter((node) => node.tag === "button" && text(node) === label).at(-1);
button("Mostrar 1 descartada").handlers.click();
assert.equal(rows().length, 5);
assert.match(rows()[4], /· descartada · Abrir.*Recuperar/);
button("Ocultar descartadas").handlers.click();
assert.equal(rows().length, 4);
// Aprobar despliega quién y canal en la misma fila, llama a la API y vuelve a pedir la lista.
const loads = editionLoads;
savedList().children[0].children.find((node) => node.tag === "button" && text(node) === "Aprobar…").handlers.click();
const [by, via] = nodes.filter((node) => node.tag === "input" && node.type === "text").slice(-2);
by.value = "Jaime"; via.value = "WhatsApp";
button("Confirmar").handlers.click();
await setImmediate();
assert.deepEqual(approvals, [{ id: 5, by: "Jaime", via: "WhatsApp" }]);
assert.equal(editionLoads, loads + 1);
// Descartar un borrador no pregunta; una aprobada que está en el sitio pide confirmación y, si se cancela, no llama a la API.
let answer = false;
globalThis.confirm = (message) => { confirms.push(message); return answer; };
const discardOf = (i) => savedList().children[i].children.find((node) => node.tag === "button" && text(node) === "Descartar");
discardOf(0).handlers.click();
await setImmediate();
assert.deepEqual([discards, confirms.length], [[5], 0]);
discardOf(1).handlers.click();
await setImmediate();
assert.deepEqual([discards, confirms.length], [[5], 1]);
assert.match(confirms[0], /en el sitio.*aprobarla de nuevo/);
answer = true;
discardOf(1).handlers.click();
await setImmediate();
assert.deepEqual([discards, confirms.length], [[5, 4], 2]);
