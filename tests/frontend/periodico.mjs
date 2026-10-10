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
const previews = [], generations = [];
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
  async getEditions() { return []; },
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
