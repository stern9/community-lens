// Construcción del DOM.
// Regla de seguridad: el contenido de los chats SOLO entra al DOM como texto (createTextNode).
// No existe ninguna ruta en este módulo que interprete cadenas como HTML.

export type Child = Node | string | number | null | undefined | false | readonly Child[];

type Handler = (ev: Event) => unknown;
export type AttrValue = string | number | boolean | null | undefined | Handler;
export type Attrs = Readonly<Record<string, AttrValue>>;

const BOOLEAN_PROPS = new Set(["checked", "disabled", "selected", "hidden", "open"]);

function appendChildren(el: Element, kids: readonly Child[]): void {
  for (const kid of kids) {
    if (kid == null || kid === false) continue;
    if (Array.isArray(kid)) appendChildren(el, kid);
    else if (kid instanceof Node) el.append(kid);
    else el.append(document.createTextNode(String(kid)));
  }
}

/** Crea un elemento. Los atributos `on…` son manejadores; los hijos se insertan siempre como nodos o texto. */
export function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs?: Attrs | null, ...kids: Child[]): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs ?? {})) {
    if (v == null || v === false) continue;
    if (typeof v === "function") {
      if (!k.startsWith("on")) throw new Error(`El atributo ${k} no admite una función`);
      el.addEventListener(k.slice(2), v);
    } else if (k === "class") {
      el.className = String(v);
    } else if (k === "value" && (el instanceof HTMLInputElement || el instanceof HTMLOptionElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement)) {
      el.value = String(v);
    } else if (BOOLEAN_PROPS.has(k)) {
      el.toggleAttribute(k, Boolean(v));
      // `checked` y `selected` como atributo solo fijan el valor inicial; se asigna también la propiedad.
      if (k === "checked" && el instanceof HTMLInputElement) el.checked = Boolean(v);
      if (k === "selected" && el instanceof HTMLOptionElement) el.selected = Boolean(v);
    } else {
      el.setAttribute(k, v === true ? "" : String(v));
    }
  }
  appendChildren(el, kids);
  return el;
}

/** Reemplaza el contenido de `el`. A diferencia de replaceChildren, aplana arreglos e ignora null/false. */
export function put(el: Element, ...kids: Child[]): void {
  el.replaceChildren();
  appendChildren(el, kids);
}

/** Busca un elemento por id y verifica su tipo; lanza si el HTML no coincide con lo esperado. */
export function byId<T extends HTMLElement>(id: string, ctor: abstract new () => T): T {
  const el = document.getElementById(id);
  if (!(el instanceof ctor)) throw new Error(`Falta el elemento #${id} o no es del tipo esperado`);
  return el;
}

export const kind = (cls: "fact" | "auto" | "pending" | "human", text: string): HTMLSpanElement =>
  h("span", { class: "kind " + cls }, text);

export interface Column {
  t: string;
  num?: boolean;
}

export function table(headers: readonly Column[], rows: readonly (readonly Child[])[]): HTMLDivElement {
  return h("div", { class: "scroll" }, h("table", null,
    h("thead", null, h("tr", null, headers.map((x) => h("th", { class: x.num ? "num" : null }, x.t)))),
    h("tbody", null, rows.map((r) => h("tr", null, r.map((c, i) => h("td", { class: headers[i]?.num ? "num" : null }, c)))))));
}

export interface BarRow {
  label: Child;
  value: number;
  right?: string;
}

/** Barras horizontales en HTML (una sola serie). El ancho se fija por CSSOM, no con atributo style. */
export function hbars(rows: readonly BarRow[], max: number, fmt: (x: number) => string): HTMLDivElement[] {
  return rows.map((r) => {
    const fill = h("div", { class: "fill" });
    fill.style.width = (max ? (r.value / max) * 100 : 0) + "%";
    return h("div", { class: "hbar" }, r.label, h("div", { class: "track" }, fill), h("span", { class: "muted" }, r.right ?? fmt(r.value)));
  });
}
