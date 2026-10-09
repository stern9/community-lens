// Enrutador: traduce el #hash de la dirección en una vista y la dibuja.
//   #/resumen?vista=dia   -> una pestaña, con sus parámetros
//   #/m/123               -> el panel de contexto del mensaje 123, sobre la pestaña en la que se estaba
import { h, put } from "./dom.js";
import { errMsg } from "./format.js";
import { drawer, view, type View } from "./shell.js";
import { viewCategorias } from "./views/categorias.js";
import { openContext } from "./views/contexto.js";
import { viewExplorar } from "./views/explorar.js";
import { viewImportar } from "./views/importar.js";
import { viewPeriodico } from "./views/periodico.js";
import { viewRecursos } from "./views/recursos.js";
import { viewReporte } from "./views/reporte.js";
import { viewResumen } from "./views/resumen.js";

/** Una entrada por pestaña de index.html (atributo data-tab). */
const VIEWS = {
  resumen: viewResumen, explorar: viewExplorar, recursos: viewRecursos, categorias: viewCategorias,
  importar: viewImportar, reporte: viewReporte, periodico: viewPeriodico,
} as const satisfies Readonly<Record<string, View>>;
type Tab = keyof typeof VIEWS;
const isTab = (x: string | undefined): x is Tab => x !== undefined && Object.hasOwn(VIEWS, x);

const DEFAULT_ROUTE = "#/resumen";
/** La última pestaña visitada: a ella se vuelve al cerrar el panel de contexto. */
let lastRoute = DEFAULT_ROUTE;
/** Cada navegación toma un número; si llega otra antes de que termine, la anterior ya no pinta sus errores. */
let routeToken = 0;

async function route(hash: string): Promise<void> {
  const [path, query] = hash.slice(2).split("?");
  const tab: Tab = isTab(path) ? path : "resumen";
  document.querySelectorAll<HTMLAnchorElement>("#tabs a[data-tab]").forEach((a) => a.classList.toggle("on", a.dataset["tab"] === tab));
  document.querySelectorAll<HTMLElement>("#tabs .mode").forEach((m) => m.classList.toggle("on", m.querySelector("a.on") !== null));
  const mine = ++routeToken;
  try { await VIEWS[tab]({ params: new URLSearchParams(query ?? ""), refresh: render }); }
  catch (e) {
    if (mine === routeToken) put(view, h("section", { class: "card" }, h("p", { class: "error" }, "Error: " + errMsg(e))));
  }
}

/** Dibuja lo que pide la dirección actual. Es también lo que las vistas reciben como `refresh`. */
export async function render(): Promise<void> {
  const hash = location.hash || DEFAULT_ROUTE;
  const message = /^#\/m\/(\d+)/.exec(hash);
  if (message?.[1]) {
    if (!view.childNodes.length) await route("#/explorar");
    await openContext(Number(message[1]), closeContext);
    return;
  }
  drawer.hidden = true;
  lastRoute = hash;
  await route(hash);
}

export function closeContext(): void {
  drawer.hidden = true;
  if (location.hash.startsWith("#/m/")) history.replaceState(null, "", lastRoute);
}
