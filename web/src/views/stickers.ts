// Stickers: los más mandados del grupo en el período, con su imagen, quién los manda y desde cuándo.
// Solo entran los que vinieron en un .zip "con archivos"; la cobertura se informa arriba para no confundir.
import * as api from "../api.js";
import { h, hbars, kind, put } from "../dom.js";
import { n } from "../format.js";
import { emptyState, view, type View } from "../shell.js";
import { scope, state } from "../state.js";
import type { StickerItem } from "../types.js";

export const viewStickers: View = async () => {
  if (state.chatId == null) return emptyState();
  const r = await api.getStickers(scope());
  const missing = r.sticker_messages - r.with_image;
  const max = Math.max(1, ...r.senders.map((s) => s.times));
  put(view,
    h("section", { class: "card" },
      h("h2", null, "Stickers", kind("fact", "hecho")),
      h("p", { class: "muted" },
        `${n(r.sticker_messages)} sticker${r.sticker_messages === 1 ? "" : "s"} en el período · ${n(r.with_image)} con imagen · ${n(r.distinct)} distintos`),
      missing > 0 ? h("div", { class: "notice" },
        (missing === 1 ? "1 sticker llegó en una exportación sin archivos: se sabe que fue un sticker, no cuál. "
          : `${n(missing)} stickers llegaron en una exportación sin archivos: se sabe que fueron stickers, no cuáles. `) +
        "Para verlos en el ranking, exportá el grupo «con archivos» e importá el .zip; los mensajes que ya estaban no se duplican.") : null,
      h("p", null, h("small", null, r.definitions["times"] ?? "", " Un sticker es el mismo si la imagen es idéntica, aunque el archivo se llame distinto."))),
    r.senders.length ? h("section", { class: "card" }, h("h3", null, "Quiénes mandan más stickers"),
      hbars(r.senders.map((s) => ({ label: s.label, value: s.times })), max, n)) : null,
    r.items.length
      ? h("section", { class: "stickers" }, r.items.map((it, i) => stickerCard(it, i + 1)))
      : h("section", { class: "card" }, h("p", { class: "muted" }, r.sticker_messages ? "Ninguno de los stickers del período tiene imagen." : "No hay stickers en el período.")));
};

function stickerCard(it: StickerItem, rank: number): HTMLElement {
  return h("article", { class: "card sticker" },
    h("span", { class: "rank muted" }, `#${rank}`),
    h("img", { src: api.stickerSrc(it.sha256), alt: `Sticker #${rank}`, loading: "lazy", width: 160, height: 160 }),
    h("b", { class: "times" }, `${n(it.times)}×`),
    h("small", null, it.senders.map((s) => `${s.label} (${n(s.times)})`).join(", ")),
    h("small", null, `${it.first_ts.slice(0, 10)} → ${it.last_ts.slice(0, 10)}`),
    h("a", { href: `#/m/${it.message_id}` }, "Ver en la conversación"));
}
