"""Búsqueda (FTS5), vista de contexto y conteos por categoría.
Qué categoría tiene un mensaje y cuál es la ejecución vigente se definen en classify/labels.py."""
from __future__ import annotations

import json
import re

from . import stats
from .classify import labels
from .classify.taxonomy import CATEGORY_KEYS

_TOKEN = re.compile(r'"([^"]+)"|(\S+)')
SNIP_A, SNIP_B = "\u0001", "\u0002"  # delimitadores de resaltado; el frontend los convierte en <mark> de forma segura

def fts_query(q: str) -> str | None:
    """Convierte texto libre en una consulta FTS5 segura: cada término entre comillas, unidos por AND."""
    parts = []
    for phrase, word in _TOKEN.findall(q or ""):
        tok = phrase or word
        prefix = bool(word) and tok.endswith("*")
        tok = re.sub(r"[^\w\s]", " ", tok, flags=re.U).strip()
        if not tok:
            continue
        parts.append('"' + tok.replace('"', '""') + '"' + ("*" if prefix else ""))
    return " ".join(parts) or None


def _serialize(con, rows, runs: dict[int, int | None]) -> list[dict]:
    """`runs` = ejecución que rige para cada chat presente en `rows`."""
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    ph = ",".join("?" * len(ids))
    tags = labels.for_messages(con, {r["id"]: runs.get(r["chat_id"]) for r in rows})
    src = {}
    for s in con.execute(
        f"""SELECT s.message_id, i.file_name, s.line_start, s.line_end, s.import_id FROM message_sources s
            JOIN imports i ON i.id = s.import_id WHERE s.message_id IN ({ph}) ORDER BY s.import_id""", ids):
        src.setdefault(s["message_id"], []).append(
            {"file_name": s["file_name"], "import_id": s["import_id"], "line_start": s["line_start"], "line_end": s["line_end"]})
    out = []
    for r in rows:
        d = {k: r[k] for k in ("id", "uid", "chat_id", "chat_name", "seq", "author_id", "author_raw", "author_label",
                               "canonical_label", "ts_local", "ts_written", "ts_utc", "record_type", "subtype", "text",
                               "media_kind", "is_deleted", "is_edited", "is_poll", "link_count", "needs_review", "review_reason")}
        d["ref"] = f"CL-{r['uid']}"
        d.update(tags[r["id"]])
        d["sources"] = src.get(r["id"], [])
        if "snippet" in r.keys():
            d["snippet"] = r["snippet"]
        out.append(d)
    return out


_SELECT = """SELECT m.id, m.uid, m.chat_id, ch.name chat_name, m.seq, m.author_id, m.author_raw,
       COALESCE(a.display_label, m.author_raw) author_label, ca.display_label canonical_label,
       m.ts_local, m.ts_written, m.ts_utc, m.record_type, m.subtype, m.text, m.media_kind,
       m.is_deleted, m.is_edited, m.is_poll, m.link_count, m.needs_review, m.review_reason"""
_FROM = """ FROM messages m JOIN chats ch ON ch.id = m.chat_id
       LEFT JOIN authors a ON a.id = m.author_id LEFT JOIN authors ca ON ca.id = a.canonical_id"""


def search(con, *, q: str | None = None, chat_id: int | None = None, d_from: str | None = None,
           d_to: str | None = None, author_id: int | None = None, category: str | None = None,
           record_type: str | None = "message", has_link: bool = False, has_media: bool = False,
           review_only: bool = False, human_only: bool = False, run_id: int | None = None,
           order: str = "recent", limit: int = 50, offset: int = 0) -> dict:
    w, p = [], []
    fq = fts_query(q) if q else None
    frm = _FROM
    sel = _SELECT
    if fq:
        frm = " FROM messages_fts f JOIN messages m ON m.id = f.rowid" + _FROM.replace(" FROM messages m", "")
        sel += f", snippet(messages_fts, 0, '{SNIP_A}', '{SNIP_B}', '…', 40) snippet"
        w.append("messages_fts MATCH ?")
        p.append(fq)
    if chat_id:
        w.append("m.chat_id = ?"); p.append(chat_id)
    if d_from:
        w.append("substr(m.ts_local,1,10) >= ?"); p.append(d_from)
    if d_to:
        w.append("substr(m.ts_local,1,10) <= ?"); p.append(d_to)
    if author_id:
        w.append("(m.author_id = ? OR a.canonical_id = ?)"); p += [author_id, author_id]
    if record_type and record_type != "all":
        w.append("m.record_type = ?"); p.append(record_type)
    if has_link:
        w.append("m.link_count > 0")
    if has_media:
        w.append("m.media_kind IS NOT NULL")
    if review_only:
        w.append("m.needs_review = 1")
    if human_only:
        w.append(f"{labels.LATEST_HUMAN} IS NOT NULL")
    if category:
        cond, params = labels.has_category(category, run_id)
        w.append(cond)
        p += params
    where = (" WHERE " + " AND ".join(w)) if w else ""
    total = con.execute("SELECT COUNT(*) n" + frm + where, p).fetchone()["n"]
    ordering = {"recent": "m.ts_utc DESC, m.seq DESC", "oldest": "m.ts_utc ASC, m.seq ASC",
                "relevance": "bm25(messages_fts), m.ts_utc DESC" if fq else "m.ts_utc DESC, m.seq DESC"}.get(order, "m.ts_utc DESC, m.seq DESC")
    limit = max(1, min(int(limit), 200))
    rows = con.execute(sel + frm + where + f" ORDER BY {ordering} LIMIT ? OFFSET ?", p + [limit, max(0, int(offset))]).fetchall()
    runs = {c: (run_id or labels.active_run(con, c)) for c in {r["chat_id"] for r in rows}}
    return {"total": total, "limit": limit, "offset": offset, "fts_query": fq, "items": _serialize(con, rows, runs)}


def context(con, message_id: int, before: int = 6, after: int = 6) -> dict:
    m = con.execute("SELECT chat_id, seq FROM messages WHERE id=?", (message_id,)).fetchone()
    if not m:
        raise KeyError("message")
    before, after = max(0, min(before, 50)), max(0, min(after, 50))
    rows = con.execute(_SELECT + _FROM + " WHERE m.chat_id=? AND m.seq BETWEEN ? AND ? ORDER BY m.seq",
                       (m["chat_id"], m["seq"] - before, m["seq"] + after)).fetchall()
    items = _serialize(con, rows, {m["chat_id"]: labels.active_run(con, m["chat_id"])})
    return {"message_id": message_id, "chat_id": m["chat_id"], "items": items,
            "note": "El TXT de WhatsApp no trae identificadores ni enlaces nativos a mensajes: esta vista es interna "
                    "de Community Lens. La ubicación citada es la línea del archivo importado."}


def _participant_where(chat_id: int, d_from: str | None, d_to: str | None) -> tuple[str, list]:
    w, p = ["m.chat_id = ?", "m.record_type = 'message'"], [chat_id]
    if d_from:
        w.append("substr(m.ts_local,1,10) >= ?"); p.append(d_from)
    if d_to:
        w.append("substr(m.ts_local,1,10) <= ?"); p.append(d_to)
    return " AND ".join(w), p


def _run(con, chat_id: int, run_id: int | None) -> dict | None:
    """La ejecución pedida (o la vigente del chat), con su configuración; None si no hay."""
    run_id = run_id or labels.active_run(con, chat_id)
    r = con.execute("SELECT * FROM analysis_runs WHERE id=? AND chat_id=?", (run_id, chat_id)).fetchone() if run_id else None
    return {**dict(r), "config": json.loads(r["config"])} if r else None


def category_summary(con, chat_id: int, d_from: str | None = None, d_to: str | None = None, run_id: int | None = None) -> dict:
    """Categorías del período: lo que asignó la ejecución, lo que corrigieron las personas y lo efectivo.
    Es la suma de los meses de category_monthly: los dos salen de labels.tally."""
    where, p = _participant_where(chat_id, d_from, d_to)
    run = _run(con, chat_id, run_id)
    months = labels.tally(con, where, p, run["id"] if run else None).values()
    total = lambda key: {k: sum(m[key][k] for m in months) for k in CATEGORY_KEYS}
    return {"run": run,
            "participant_messages": con.execute(f"SELECT COUNT(*) n FROM messages m WHERE {where}", p).fetchone()["n"],
            "classified_messages": sum(m["classified"] for m in months),
            "corrected_messages": sum(m["corrected"] for m in months),
            "auto": total("auto"), "human": total("human"), "effective": total("effective"),
            "note": "Un mensaje puede tener varias categorías, por lo que los conteos por categoría no suman el total. "
                    "Los mensajes sin texto (multimedia omitida, eliminados) no se clasifican. Un mensaje corregido a mano "
                    "cuenta como clasificado aunque la ejecución no lo haya cubierto."}


def category_monthly(con, chat_id: int, d_from: str | None = None, d_to: str | None = None, run_id: int | None = None) -> dict:
    """Categorías efectivas por mes (corrección humana si existe; si no, la predicción de la ejecución).

    El denominador de cada mes son los mensajes CLASIFICADOS de ese mes, no todos los mensajes: una ejecución
    puede cubrir solo parte del período (p. ej. el tope de Jev)."""
    where, p = _participant_where(chat_id, d_from, d_to)
    run = _run(con, chat_id, run_id)
    tally = labels.tally(con, where, p, run["id"] if run else None)
    if not run and not tally:
        return {"run": None, "months": []}
    window = stats.month_window(con, chat_id, d_from, d_to)  # mes parcial: mismo criterio que las estadísticas oficiales
    out = []
    for r in con.execute(f"SELECT substr(m.ts_local,1,7) mo, COUNT(*) n FROM messages m WHERE {where} GROUP BY mo ORDER BY mo", p):
        t = tally.get(r["mo"])
        classified, counts = (t["classified"], t["effective"]) if t else (0, {k: 0 for k in CATEGORY_KEYS})
        out.append({"month": r["mo"], "participant_messages": r["n"], "classified": classified, "counts": counts,
                    "shares": {k: (round(v / classified, 4) if classified else None) for k, v in counts.items()},
                    "partial": window(r["mo"])[0]})
    return {"run": run, "months": out,
            "note": "Porcentaje sobre los mensajes clasificados de cada mes. Un mensaje puede tener varias "
                    "categorías, así que los porcentajes de un mes no suman 100."}
