"""Stickers del grupo: cuáles se mandan más, quién y cuándo. Solo SQL determinista, como stats.py.

Dos cosas distintas que no se mezclan:
- Que un mensaje fue un sticker lo sabe cualquier exportación («sticker omitido» o «<attached: …-STICKER-….webp>»).
- Cuál sticker fue solo se sabe si el mensaje vino en un .zip "con archivos": la imagen se identifica por su
  contenido (sha256), así el mismo sticker reenviado cuenta junto aunque el archivo tenga otro nombre.
El ranking cuenta solo los mensajes con imagen; el denominador de esa cobertura se informa al lado.
"""
from __future__ import annotations

from .stats import AUTHOR_KEY, _where

DEFINITIONS = {
    "sticker_messages": "Mensajes de participantes que son un sticker, con o sin imagen en la base.",
    "with_image": "Stickers del período cuya imagen se importó desde un .zip exportado con archivos. Solo estos entran "
                  "al ranking; los que llegaron en una exportación sin archivos se cuentan, pero no se sabe cuál son.",
    "distinct": "Imágenes distintas por contenido entre los stickers con imagen.",
    "times": "Veces que se mandó ese sticker en el período: cada mensaje cuenta una vez.",
}


def ranking(con, chat_id: int, d_from: str | None = None, d_to: str | None = None, limit: int = 60) -> dict:
    w, p = _where(chat_id, d_from, d_to)
    wm = w + " AND m.record_type = 'message'"
    total = con.execute(f"SELECT COUNT(*) n FROM messages m WHERE {wm} AND m.media_kind = 'sticker'", p).fetchone()["n"]
    cov = con.execute(f"""SELECT COUNT(*) n, COUNT(DISTINCT ms.sha256) d FROM message_stickers ms
                          JOIN messages m ON m.id = ms.message_id WHERE {wm}""", p).fetchone()
    rows = con.execute(
        f"""SELECT ms.sha256, COUNT(*) n, MIN(m.ts_local) first_ts, MAX(m.ts_local) last_ts
            FROM message_stickers ms JOIN messages m ON m.id = ms.message_id
            WHERE {wm} GROUP BY ms.sha256 ORDER BY n DESC, first_ts, ms.sha256 LIMIT ?""", [*p, limit]).fetchall()

    labels = {r["id"]: r["display_label"] for r in con.execute("SELECT id, display_label FROM authors WHERE chat_id=?", (chat_id,))}
    senders: dict[str, list[dict]] = {}
    latest: dict[str, int] = {}
    shas = [r["sha256"] for r in rows]
    if shas:
        marks = ",".join("?" * len(shas))
        for r in con.execute(
                f"""SELECT ms.sha256, {AUTHOR_KEY} k, COUNT(*) n FROM message_stickers ms
                    JOIN messages m ON m.id = ms.message_id JOIN authors a ON a.id = m.author_id
                    WHERE {wm} AND ms.sha256 IN ({marks}) GROUP BY ms.sha256, k ORDER BY n DESC, k""", [*p, *shas]):
            senders.setdefault(r["sha256"], []).append({"author_id": r["k"], "label": labels.get(r["k"], "—"), "times": r["n"]})
        # El envío más reciente de cada uno, para abrirlo en la conversación.
        for r in con.execute(
                f"""SELECT ms.sha256, m.id FROM message_stickers ms JOIN messages m ON m.id = ms.message_id
                    WHERE {wm} AND ms.sha256 IN ({marks}) ORDER BY m.ts_local, m.seq""", [*p, *shas]):
            latest[r["sha256"]] = r["id"]

    top_senders = [{"author_id": r["k"], "label": labels.get(r["k"], "—"), "times": r["n"]} for r in con.execute(
        f"""SELECT {AUTHOR_KEY} k, COUNT(*) n FROM messages m JOIN authors a ON a.id = m.author_id
            WHERE {wm} AND m.media_kind = 'sticker' GROUP BY k ORDER BY n DESC, k LIMIT 10""", p)]

    return {
        "sticker_messages": total, "with_image": cov["n"], "distinct": cov["d"],
        "items": [{"sha256": r["sha256"], "times": r["n"], "first_ts": r["first_ts"], "last_ts": r["last_ts"],
                   "message_id": latest[r["sha256"]], "senders": senders.get(r["sha256"], [])[:3]} for r in rows],
        "senders": top_senders, "definitions": DEFINITIONS,
    }


def image(con, sha256: str) -> bytes | None:
    row = con.execute("SELECT bytes FROM sticker_images WHERE sha256=?", (sha256,)).fetchone()
    return bytes(row["bytes"]) if row else None
