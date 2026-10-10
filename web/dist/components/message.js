// Tarjeta de un mensaje: texto, etiquetas, corrección de categorías y referencia copiable.
// La usan Explorar y el panel de contexto.
import * as api from "../api.js";
import { h, put } from "../dom.js";
import { PROVIDER, REVIEW } from "../labels.js";
import { toast } from "../shell.js";
import { catLabel, categories } from "../state.js";
/** El backend delimita coincidencias con U+0001 … U+0002; se convierten en <mark> con nodos de texto. */
function snippetNodes(snippet) {
    const out = [];
    for (const part of snippet.split("\u0001")) {
        const i = part.indexOf("\u0002");
        if (i === -1)
            out.push(part);
        else
            out.push(h("mark", null, part.slice(0, i)), part.slice(i + 1));
    }
    return out;
}
function reference(m) {
    const quote = m.text.replace(/[\u200e\u200f\u202a-\u202c\u2066-\u2069]/g, "").trim();
    const src = m.sources[0];
    const lines = src ? ` · ${src.file_name}, línea${src.line_end > src.line_start ? "s" : ""} ${src.line_start}${src.line_end > src.line_start ? "–" + src.line_end : ""}` : "";
    return `«${quote.length > 400 ? quote.slice(0, 400) + "…" : quote}»\n— ${m.author_label ?? "Aviso del sistema"}, ${m.chat_name}, ${m.ts_local.slice(0, 16)}` +
        `\nRef. ${m.ref}${lines}` +
        `\n${location.origin}/#/m/${m.id} (enlace interno de Community Lens; WhatsApp no ofrece enlaces a mensajes en el TXT)`;
}
export function messageCard(m, opts) {
    const { target = false, full = false, onChange } = opts;
    const isMsg = m.record_type === "message";
    const body = h("div", { class: "body" + (full ? "" : " clamp") }, m.snippet && !full ? snippetNodes(m.snippet) : m.text);
    const assigned = m.auto.filter((a) => a.assigned);
    const chips = [];
    if (m.human) {
        m.human.categories.forEach((c) => chips.push(h("span", { class: "chip human", title: "Corrección humana" }, catLabel(c) + " · revisado")));
        if (assigned.length)
            chips.push(h("span", { class: "chip", title: "Predicción original conservada" }, "automático decía: " + assigned.map((a) => catLabel(a.category)).join(", ")));
    }
    else {
        assigned.forEach((a) => chips.push(h("span", {
            class: "chip auto",
            title: a.evidence?.rules ? "Reglas: " + (a.evidence.rules[a.category] ?? []).join(", ") : "Clasificación automática",
        }, `${catLabel(a.category)} · ${PROVIDER[a.provider]}` + (a.score != null ? ` · noul ${a.score.toFixed(2)}` : ""))));
    }
    if (m.needs_review)
        chips.push(h("span", { class: "chip flag" }, "revisar: " + (m.review_reason ? REVIEW[m.review_reason] ?? m.review_reason : "sin motivo")));
    if (m.media_kind)
        chips.push(h("span", { class: "chip" }, "adjunto no disponible: " + m.media_kind));
    if (m.is_deleted)
        chips.push(h("span", { class: "chip" }, "eliminado"));
    if (m.is_edited)
        chips.push(h("span", { class: "chip" }, "editado"));
    if (!isMsg)
        chips.push(h("span", { class: "chip" }, m.record_type === "system" ? "aviso del sistema" : "no reconocido"));
    const editor = h("div", { hidden: true });
    const edit = () => {
        const boxes = categories().map((c) => ({ key: c.key, input: h("input", { type: "checkbox", checked: m.effective.includes(c.key) }), label: c.label }));
        const save = async (cats) => {
            await api.setLabels(m.id, cats);
            toast(cats ? "Corrección guardada; la predicción original se conserva." : "Se volvió a la clasificación automática.");
            await onChange();
        };
        put(editor, h("fieldset", null, h("legend", null, "Categorías correctas para este mensaje"), h("div", { class: "checks" }, boxes.map((b) => h("label", { class: "row" }, b.input, b.label))), h("div", { class: "actions" }, h("button", { class: "small", onclick: () => void save(boxes.filter((b) => b.input.checked).map((b) => b.key)) }, "Guardar corrección"), m.human ? h("button", { class: "ghost small", onclick: () => void save(null) }, "Volver a la automática") : null, h("button", { class: "ghost small", onclick: () => { editor.hidden = true; } }, "Cancelar"))));
        editor.hidden = false;
    };
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(reference(m));
            toast("Referencia copiada.");
        }
        catch {
            prompt("Copiá la referencia:", reference(m));
        }
    };
    return h("article", { class: "msg" + (target ? " target" : "") + (isMsg ? "" : " system") }, h("div", { class: "meta" }, h("b", null, m.author_label ?? "Sistema"), m.canonical_label ? h("span", null, `(alias de ${m.canonical_label})`) : null, h("span", null, m.ts_local.slice(0, 16)), h("span", null, m.chat_name)), body, chips.length ? h("div", { class: "chips" }, chips) : null, h("div", { class: "actions" }, target ? null : h("a", { href: `#/m/${m.id}`, class: "btnlink" }, "Ver contexto"), h("button", { class: "ghost small", onclick: () => void copy() }, "Copiar referencia"), isMsg && m.text ? h("button", { class: "ghost small", onclick: edit }, "Corregir categorías") : null), editor);
}
