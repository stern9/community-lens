// Panel lateral con un mensaje y lo que se dijo antes y después.
import * as api from "../api.js";
import { messageCard } from "../components/message.js";
import { h, put } from "../dom.js";
import { errMsg, n } from "../format.js";
import { drawer } from "../shell.js";
export async function openContext(id, close) {
    let before = 6, after = 6;
    const closeButton = () => h("button", { class: "ghost", onclick: close }, "Cerrar");
    async function draw() {
        const r = await api.getContext(id, before, after);
        const target = r.items.find((m) => m.id === id);
        if (!target)
            throw new Error("El mensaje no aparece en su propio contexto");
        put(drawer, h("header", null, h("h2", null, "Contexto del mensaje"), closeButton()), h("p", null, h("small", null, `${target.chat_name} · Ref. ${target.ref} · posición ${n(target.seq + 1)} en la secuencia. `, target.sources.map((s) => `${s.file_name}, líneas ${s.line_start}–${s.line_end}`).join(" · "), ". ", r.note)), h("p", null, h("button", { class: "ghost small", onclick: () => { before += 10; void draw(); } }, "↑ Ver anteriores")), r.items.map((m) => messageCard(m, { target: m.id === id, full: true, onChange: draw })), h("p", null, h("button", { class: "ghost small", onclick: () => { after += 10; void draw(); } }, "↓ Ver siguientes")));
        drawer.querySelector(".target")?.scrollIntoView({ block: "center" });
    }
    drawer.hidden = false;
    try {
        await draw();
    }
    catch (e) {
        put(drawer, h("p", { class: "error" }, errMsg(e)), closeButton());
    }
}
