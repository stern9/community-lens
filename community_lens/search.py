"""Búsqueda (FTS5), vista de contexto y categorías efectivas."""
from __future__ import annotations

import json
import re

from .classify.taxonomy import CATEGORY_KEYS

_TOKEN = re.compile(r'"([^"]+)"|(\S+)')
SNIP_A, SNIP_B = "\u0001", "\u0002"  # delimitadores de resaltado; el frontend los convierte en <mark> de forma segura

# Última corrección humana de un mensaje (NULL si no hay o si se revirtió).
_LATEST_HUMAN = "(SELECT h.categories FROM human_labels h WHERE h.message_id = m.id ORDER BY h.id DESC LIMIT 1)"


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


# Ejecución vigente de un chat: la elegida explícitamente si sigue existiendo; si no, la más reciente.
_OK = "status IN ('completed','partial')"
CURRENT_RUN_SQL = (
    "COALESCE((SELECT r.id FROM chats c2 JOIN analysis_runs r ON r.id = c2.active_run_id AND r.chat_id = c2.id "
    f"WHERE c2.id = m.chat_id AND r.{_OK}), "
    f"(SELECT MAX(r.id) FROM analysis_runs r WHERE r.chat_id = m.chat_id AND r.{_OK}))")


def latest_run(con, chat_id: int) -> int | None:
    """Ejecución vigente del chat (el nombre se conserva por compatibilidad)."""
    r = con.execute(
        f"""SELECT COALESCE((SELECT r.id FROM chats c JOIN analysis_runs r ON r.id = c.active_run_id AND r.chat_id = c.id
                             WHERE c.id = ? AND r.{_OK}),
                            (SELECT MAX(id) FROM analysis_runs WHERE chat_id = ? AND {_OK})) i""", (chat_id, chat_id)).fetchone()
    return r["i"]


def set_active_run(con, chat_id: int, run_id: int) -> None:
    ok = con.execute(f"SELECT 1 FROM analysis_runs WHERE id=? AND chat_id=? AND {_OK}", (run_id, chat_id)).fetchone()
    if not ok:
        raise KeyError("run")
    con.execute("UPDATE chats SET active_run_id=? WHERE id=?", (run_id, chat_id))


def _serialize(con, rows, runs: dict[int, int | None]) -> list[dict]:
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    ph = ",".join("?" * len(ids))
    auto: dict[int, list] = {}
    run_ids = [x for x in set(runs.values()) if x]
    if run_ids:
        for c in con.execute(
            f"""SELECT c.message_id, c.category, c.assigned, c.score, c.evidence, c.run_id, r.provider, r.model
                FROM classifications c JOIN analysis_runs r ON r.id = c.run_id
                WHERE c.message_id IN ({ph}) AND c.run_id IN ({",".join("?" * len(run_ids))})""", ids + run_ids):
            auto.setdefault(c["message_id"], []).append({
                "category": c["category"], "assigned": bool(c["assigned"]), "score": c["score"],
                "provider": c["provider"], "model": c["model"], "run_id": c["run_id"],
                "evidence": json.loads(c["evidence"]) if c["evidence"] else None})
    human = {}
    for h in con.execute(f"SELECT message_id, categories, note, created_at FROM human_labels WHERE message_id IN ({ph}) ORDER BY id", ids):
        human[h["message_id"]] = h
    src = {}
    for s in con.execute(
        f"""SELECT s.message_id, i.file_name, s.line_start, s.line_end, s.import_id FROM message_sources s
            JOIN imports i ON i.id = s.import_id WHERE s.message_id IN ({ph}) ORDER BY s.import_id""", ids):
        src.setdefault(s["message_id"], []).append(
            {"file_name": s["file_name"], "import_id": s["import_id"], "line_start": s["line_start"], "line_end": s["line_end"]})
    out = []
    for r in rows:
        h = human.get(r["id"])
        hcats = json.loads(h["categories"]) if h and h["categories"] is not None else None
        a = sorted(auto.get(r["id"], []), key=lambda x: CATEGORY_KEYS.index(x["category"]) if x["category"] in CATEGORY_KEYS else 99)
        d = {k: r[k] for k in ("id", "uid", "chat_id", "chat_name", "seq", "author_id", "author_raw", "author_label",
                               "canonical_label", "ts_local", "ts_written", "ts_utc", "record_type", "subtype", "text",
                               "media_kind", "is_deleted", "is_edited", "is_poll", "link_count", "needs_review", "review_reason")}
        d["ref"] = f"CL-{r['uid']}"
        d["auto"] = a
        d["human"] = {"categories": hcats, "note": h["note"], "created_at": h["created_at"]} if hcats is not None else None
        d["effective"] = hcats if hcats is not None else [x["category"] for x in a if x["assigned"]]
        d["effective_source"] = "human" if hcats is not None else ("auto" if a else None)
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
        w.append(f"{_LATEST_HUMAN} IS NOT NULL")
    if category:
        if category not in CATEGORY_KEYS:
            raise ValueError("categoría desconocida")
        # Categoría efectiva: la corrección humana manda; si no hay, la predicción
        # de la última ejecución del chat (o la indicada).
        run_expr = "?" if run_id else CURRENT_RUN_SQL
        w.append(f"""(CASE WHEN {_LATEST_HUMAN} IS NOT NULL
                 THEN EXISTS (SELECT 1 FROM json_each({_LATEST_HUMAN}) j WHERE j.value = ?)
                 ELSE EXISTS (SELECT 1 FROM classifications c WHERE c.message_id = m.id AND c.assigned = 1
                              AND c.category = ? AND c.run_id = {run_expr}) END)""")
        p += [category, category] + ([run_id] if run_id else [])
    where = (" WHERE " + " AND ".join(w)) if w else ""
    total = con.execute("SELECT COUNT(*) n" + frm + where, p).fetchone()["n"]
    ordering = {"recent": "m.ts_utc DESC, m.seq DESC", "oldest": "m.ts_utc ASC, m.seq ASC",
                "relevance": "bm25(messages_fts), m.ts_utc DESC" if fq else "m.ts_utc DESC, m.seq DESC"}.get(order, "m.ts_utc DESC, m.seq DESC")
    limit = max(1, min(int(limit), 200))
    rows = con.execute(sel + frm + where + f" ORDER BY {ordering} LIMIT ? OFFSET ?", p + [limit, max(0, int(offset))]).fetchall()
    runs = {c: (run_id or latest_run(con, c)) for c in {r["chat_id"] for r in rows}}
    return {"total": total, "limit": limit, "offset": offset, "fts_query": fq, "items": _serialize(con, rows, runs)}


def context(con, message_id: int, before: int = 6, after: int = 6) -> dict:
    m = con.execute("SELECT chat_id, seq FROM messages WHERE id=?", (message_id,)).fetchone()
    if not m:
        raise KeyError("message")
    before, after = max(0, min(before, 50)), max(0, min(after, 50))
    rows = con.execute(_SELECT + _FROM + " WHERE m.chat_id=? AND m.seq BETWEEN ? AND ? ORDER BY m.seq",
                       (m["chat_id"], m["seq"] - before, m["seq"] + after)).fetchall()
    items = _serialize(con, rows, {m["chat_id"]: latest_run(con, m["chat_id"])})
    return {"message_id": message_id, "chat_id": m["chat_id"], "items": items,
            "note": "El TXT de WhatsApp no trae identificadores ni enlaces nativos a mensajes: esta vista es interna "
                    "de Community Lens. La ubicación citada es la línea del archivo importado."}


def category_summary(con, chat_id: int, d_from: str | None = None, d_to: str | None = None, run_id: int | None = None) -> dict:
    run_id = run_id or latest_run(con, chat_id)
    w, p = ["m.chat_id = ?", "m.record_type = 'message'"], [chat_id]
    if d_from:
        w.append("substr(m.ts_local,1,10) >= ?"); p.append(d_from)
    if d_to:
        w.append("substr(m.ts_local,1,10) <= ?"); p.append(d_to)
    where = " AND ".join(w)
    participant = con.execute(f"SELECT COUNT(*) n FROM messages m WHERE {where}", p).fetchone()["n"]
    run = None
    auto = {k: 0 for k in CATEGORY_KEYS}
    classified = 0
    if run_id:
        r = con.execute("SELECT * FROM analysis_runs WHERE id=? AND chat_id=?", (run_id, chat_id)).fetchone()
        if r:
            run = {**dict(r), "config": json.loads(r["config"])}
            for c in con.execute(
                f"""SELECT c.category, COUNT(*) n FROM classifications c JOIN messages m ON m.id = c.message_id
                    WHERE c.run_id = ? AND c.assigned = 1 AND {where} GROUP BY c.category""", [run_id] + p):
                auto[c["category"]] = c["n"]
            classified = con.execute(
                f"""SELECT COUNT(DISTINCT c.message_id) n FROM classifications c JOIN messages m ON m.id = c.message_id
                    WHERE c.run_id = ? AND {where}""", [run_id] + p).fetchone()["n"]
    effective = dict(auto)
    human = {k: 0 for k in CATEGORY_KEYS}
    corrected = 0
    for h in con.execute(
        f"""SELECT m.id, {_LATEST_HUMAN} cats FROM messages m
            WHERE {where} AND {_LATEST_HUMAN} IS NOT NULL""", p):
        corrected += 1
        cats = json.loads(h["cats"])
        prev = [c["category"] for c in con.execute(
            "SELECT category FROM classifications WHERE run_id=? AND message_id=? AND assigned=1", (run_id, h["id"]))] if run_id else []
        for c in prev:
            effective[c] -= 1
        for c in cats:
            if c in effective:
                effective[c] += 1
                human[c] += 1
    return {"run": run, "participant_messages": participant, "classified_messages": classified,
            "corrected_messages": corrected, "auto": auto, "human": human, "effective": effective,
            "note": "Un mensaje puede tener varias categorías, por lo que los conteos por categoría no suman el total. "
                    "Los mensajes sin texto (multimedia omitida, eliminados) no se clasifican."}


def category_monthly(con, chat_id: int, d_from: str | None = None, d_to: str | None = None, run_id: int | None = None) -> dict:
    """Categorías efectivas por mes (corrección humana si existe; si no, la predicción de la ejecución).

    El denominador de cada mes son los mensajes CLASIFICADOS de ese mes en esa ejecución, no todos los
    mensajes: una ejecución puede cubrir solo parte del período (p. ej. el tope de Jev)."""
    run_id = run_id or latest_run(con, chat_id)
    w, p = ["m.chat_id = ?", "m.record_type = 'message'"], [chat_id]
    if d_from:
        w.append("substr(m.ts_local,1,10) >= ?"); p.append(d_from)
    if d_to:
        w.append("substr(m.ts_local,1,10) <= ?"); p.append(d_to)
    where = " AND ".join(w)
    run = con.execute("SELECT * FROM analysis_runs WHERE id=? AND chat_id=?", (run_id, chat_id)).fetchone() if run_id else None
    if not run:
        return {"run": None, "months": []}
    months: dict[str, dict] = {}

    def slot(month: str) -> dict:
        return months.setdefault(month, {"month": month, "participant_messages": 0, "classified": 0,
                                         "counts": {k: 0 for k in CATEGORY_KEYS}})

    for r in con.execute(f"SELECT substr(m.ts_local,1,7) mo, COUNT(*) n FROM messages m WHERE {where} GROUP BY mo", p):
        slot(r["mo"])["participant_messages"] = r["n"]
    for r in con.execute(
        f"""SELECT substr(m.ts_local,1,7) mo, COUNT(DISTINCT c.message_id) n
            FROM classifications c JOIN messages m ON m.id = c.message_id
            WHERE c.run_id = ? AND {where} GROUP BY mo""", [run_id] + p):
        slot(r["mo"])["classified"] = r["n"]
    # Predicción automática, salvo donde hay una corrección humana vigente.
    for r in con.execute(
        f"""SELECT substr(m.ts_local,1,7) mo, c.category, COUNT(*) n
            FROM classifications c JOIN messages m ON m.id = c.message_id
            WHERE c.run_id = ? AND c.assigned = 1 AND {where} AND {_LATEST_HUMAN} IS NULL
            GROUP BY mo, c.category""", [run_id] + p):
        if r["category"] in CATEGORY_KEYS:
            slot(r["mo"])["counts"][r["category"]] += r["n"]
    for r in con.execute(
        f"""SELECT substr(m.ts_local,1,7) mo, {_LATEST_HUMAN} cats,
                   EXISTS (SELECT 1 FROM classifications c WHERE c.run_id = ? AND c.message_id = m.id) in_run
            FROM messages m WHERE {where} AND {_LATEST_HUMAN} IS NOT NULL""", [run_id] + p):
        sl = slot(r["mo"])
        if not r["in_run"]:
            sl["classified"] += 1  # etiquetado a mano aunque la ejecución no lo cubriera
        for c in json.loads(r["cats"]):
            if c in CATEGORY_KEYS:
                sl["counts"][c] += 1
    out = []
    for mo in sorted(months):
        sl = months[mo]
        sl["shares"] = {k: (round(v / sl["classified"], 4) if sl["classified"] else None) for k, v in sl["counts"].items()}
        out.append(sl)
    return {"run": {**dict(run), "config": json.loads(run["config"])}, "months": out,
            "note": "Porcentaje sobre los mensajes clasificados de cada mes. Un mensaje puede tener varias "
                    "categorías, así que los porcentajes de un mes no suman 100."}
