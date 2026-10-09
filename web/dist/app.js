// Community Lens — punto de entrada: carga lo inicial y conecta la barra superior con el enrutador.
// Se compila con `npm run build` a web/dist (versionado, para que la app corra sin Node).
//
//   app.ts        arranque
//   router.ts     #hash -> vista
//   views/        una pantalla por archivo
//   components/   piezas que usan varias pantallas
//   state.ts      grupo y período elegidos        shell.ts   marco fijo de la página
//   api.ts        cliente de la API (types.ts)    dom.ts, charts.ts, format.ts, labels.ts   utilidades
import * as api from "./api.js";
import { byId, h, put } from "./dom.js";
import { errMsg } from "./format.js";
import { closeContext, render } from "./router.js";
import { drawer, syncScope, view } from "./shell.js";
import { loadChats, state } from "./state.js";
async function init() {
    const cfg = await api.getConfig();
    state.config = cfg;
    if (!cfg.loopback) {
        const badge = byId("badge", HTMLElement);
        badge.textContent = "expuesta en red";
        badge.classList.add("error");
    }
    try {
        state.chatId = Number(localStorage.getItem("cl.chat")) || null;
    }
    catch { /* sin almacenamiento */ }
    await loadChats();
    syncScope();
    bindScopeBar();
    window.addEventListener("hashchange", () => void render());
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !drawer.hidden)
        closeContext(); });
    if (!state.chats.length && !location.hash)
        location.hash = "#/importar";
    else
        await render();
}
/** Cambiar de grupo o de fechas actualiza el estado y vuelve a dibujar la pantalla actual. */
function bindScopeBar() {
    const chatSel = byId("chat", HTMLSelectElement), from = byId("from", HTMLInputElement), to = byId("to", HTMLInputElement);
    chatSel.addEventListener("change", () => { state.chatId = Number(chatSel.value) || null; state.authors = []; syncScope(); void render(); });
    from.addEventListener("change", () => { state.from = from.value; void render(); });
    to.addEventListener("change", () => { state.to = to.value; void render(); });
    byId("allTime", HTMLButtonElement).addEventListener("click", () => { state.from = ""; state.to = ""; syncScope(); void render(); });
}
init().catch((e) => {
    put(view, h("section", { class: "card" }, h("p", { class: "error" }, "No se pudo iniciar la app: " + errMsg(e))));
});
