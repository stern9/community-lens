"""Etiquetas de un mensaje. Es la única definición de tres cosas que usa el resto de la app:

- Ejecución vigente de un chat: la elegida (chats.active_run_id) si sigue existiendo y terminó; si no, la más
  reciente que terminó. Se lee y se cambia solo desde este archivo.
- Categoría efectiva de un mensaje: manda la corrección humana más reciente; si no hay, o se revirtió, lo que le
  asignó la ejecución vigente.
- Mensaje clasificado: el que la ejecución cubrió o el que tiene una corrección humana vigente.

Las correcciones son un historial de solo-agregado (human_labels): la predicción original nunca se modifica.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from .taxonomy import CATEGORY_KEYS

_FINISHED = "status IN ('completed','partial')"
# Fragmentos para consultas sobre `messages m`.
# Última corrección humana del mensaje (NULL si no hay o si se revirtió).
LATEST_HUMAN = "(SELECT h.categories FROM human_labels h WHERE h.message_id = m.id ORDER BY h.id DESC LIMIT 1)"


def _active_run_sql(chat: str, but: str | None = None) -> str:
    """Expresión SQL con la ejecución vigente del chat `chat`. `but` deja afuera una ejecución al buscar la más reciente."""
    skip = f" AND r.id <> {but}" if but else ""
    return (f"COALESCE((SELECT ar.id FROM chats ac JOIN analysis_runs ar ON ar.id = ac.active_run_id AND ar.chat_id = ac.id "
            f"WHERE ac.id = {chat} AND ar.{_FINISHED}), "
            f"(SELECT MAX(r.id) FROM analysis_runs r WHERE r.chat_id = {chat} AND r.{_FINISHED}{skip}))")


# ------------------------------------------------------ ejecución vigente --

def active_run(con, chat_id: int) -> int | None:
    return con.execute(f"SELECT {_active_run_sql('?1')} i", (chat_id,)).fetchone()["i"]


def set_active_run(con, chat_id: int, run_id: int) -> None:
    """Elige a mano la ejecución vigente. KeyError si no es una ejecución terminada de ese chat."""
    ok = con.execute(f"SELECT 1 FROM analysis_runs WHERE id=? AND chat_id=? AND {_FINISHED}", (run_id, chat_id)).fetchone()
    if not ok:
        raise KeyError("run")
    con.execute("UPDATE chats SET active_run_id=? WHERE id=?", (run_id, chat_id))


def activate_if_fuller(con, chat_id: int, run_id: int, n_messages: int) -> tuple[bool, int]:
    """Al terminar una ejecución: pasa a ser la vigente solo si cubre al menos tantos mensajes como la vigente
    actual. Así una corrida parcial (tope de Jev, período acotado) no tapa una completa.
    Devuelve (¿quedó vigente?, id de la vigente)."""
    cur = con.execute(f"SELECT id, n_messages FROM analysis_runs WHERE id = {_active_run_sql('?1', but='?2')}",
                      (chat_id, run_id)).fetchone()
    activated = cur is None or n_messages >= cur["n_messages"]
    active = run_id if activated else cur["id"]
    con.execute("UPDATE chats SET active_run_id=? WHERE id=?", (active, chat_id))
    return activated, active


# ----------------------------------------------------- categoría efectiva --

def effective(human: list[str] | None, auto: list[dict]) -> tuple[list[str], str | None]:
    """(categorías efectivas, origen) de un mensaje: 'human', 'auto' o None si nadie lo clasificó."""
    if human is not None:
        return human, "human"
    return [x["category"] for x in auto if x["assigned"]], ("auto" if auto else None)


def for_messages(con, runs: dict[int, int]) -> dict[int, dict]:
    """Etiquetas de un grupo de mensajes. `runs` = {id del mensaje: ejecución que le corresponde (o None)}.
    Por mensaje: auto (predicción de esa ejecución), human (corrección vigente o None), effective y effective_source."""
    ids = list(runs)
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    auto: dict[int, list] = {}
    run_ids = [x for x in set(runs.values()) if x]
    if run_ids:
        for c in con.execute(
            f"""SELECT c.message_id, c.category, c.assigned, c.score, c.evidence, c.run_id, r.provider, r.model
                FROM classifications c JOIN analysis_runs r ON r.id = c.run_id
                WHERE c.message_id IN ({ph}) AND c.run_id IN ({",".join("?" * len(run_ids))})""", ids + run_ids):
            if c["run_id"] == runs[c["message_id"]]:
                auto.setdefault(c["message_id"], []).append({
                    "category": c["category"], "assigned": bool(c["assigned"]), "score": c["score"],
                    "provider": c["provider"], "model": c["model"], "run_id": c["run_id"],
                    "evidence": json.loads(c["evidence"]) if c["evidence"] else None})
    human = {}
    for h in con.execute(f"SELECT message_id, categories, note, created_at FROM human_labels WHERE message_id IN ({ph}) ORDER BY id", ids):
        human[h["message_id"]] = h  # queda la última
    out = {}
    for mid in ids:
        h = human.get(mid)
        hcats = json.loads(h["categories"]) if h and h["categories"] is not None else None
        a = sorted(auto.get(mid, []), key=lambda x: CATEGORY_KEYS.index(x["category"]) if x["category"] in CATEGORY_KEYS else 99)
        cats, source = effective(hcats, a)
        out[mid] = {"auto": a, "human": {"categories": hcats, "note": h["note"], "created_at": h["created_at"]} if hcats is not None else None,
                    "effective": cats, "effective_source": source}
    return out


def has_category(category: str, run_id: int | None = None) -> tuple[str, list]:
    """Condición SQL (y sus parámetros) "el mensaje m tiene esta categoría efectiva", según la ejecución
    vigente de su chat o la indicada."""
    if category not in CATEGORY_KEYS:
        raise ValueError("categoría desconocida")
    run = "?" if run_id else _active_run_sql("m.chat_id")
    return (f"""(CASE WHEN {LATEST_HUMAN} IS NOT NULL
                 THEN EXISTS (SELECT 1 FROM json_each({LATEST_HUMAN}) j WHERE j.value = ?)
                 ELSE EXISTS (SELECT 1 FROM classifications c WHERE c.message_id = m.id AND c.assigned = 1
                              AND c.category = ? AND c.run_id = {run}) END)""",
            [category, category] + ([run_id] if run_id else []))


def tally(con, where: str, params: list, run_id: int | None) -> dict[str, dict]:
    """Conteos por mes de los mensajes `m` que cumplen `where`, según la ejecución `run_id` (None = ninguna):

      {"AAAA-MM": {"classified": n, "corrected": n, "auto": {cat: n}, "human": {cat: n}, "effective": {cat: n}}}

    auto = lo que asignó la ejecución; human = lo que dicen las correcciones vigentes; effective = human donde hay
    corrección y auto donde no. El resumen del período es la suma de los meses: salen de acá los dos."""
    months: dict[str, dict] = {}

    def slot(month: str) -> dict:
        return months.setdefault(month, {"classified": 0, "corrected": 0, **{k: {c: 0 for c in CATEGORY_KEYS} for k in ("auto", "human", "effective")}})

    in_run = "EXISTS (SELECT 1 FROM classifications c WHERE c.run_id = ? AND c.message_id = m.id)"
    for r in con.execute(
        f"""SELECT substr(m.ts_local,1,7) mo, COUNT(*) n FROM messages m
            WHERE {where} AND ({in_run} OR {LATEST_HUMAN} IS NOT NULL) GROUP BY mo""", params + [run_id]):
        slot(r["mo"])["classified"] = r["n"]
    for r in con.execute(
        f"""SELECT substr(m.ts_local,1,7) mo, c.category, COUNT(*) n, SUM({LATEST_HUMAN} IS NULL) uncorrected
            FROM classifications c JOIN messages m ON m.id = c.message_id
            WHERE c.run_id = ? AND c.assigned = 1 AND {where} GROUP BY mo, c.category""", [run_id] + params):
        if r["category"] in CATEGORY_KEYS:
            slot(r["mo"])["auto"][r["category"]] += r["n"]
            slot(r["mo"])["effective"][r["category"]] += r["uncorrected"]
    for r in con.execute(
        f"""SELECT substr(m.ts_local,1,7) mo, {LATEST_HUMAN} cats FROM messages m
            WHERE {where} AND {LATEST_HUMAN} IS NOT NULL""", params):
        s = slot(r["mo"])
        s["corrected"] += 1
        for c in json.loads(r["cats"]):
            if c in CATEGORY_KEYS:
                s["human"][c] += 1
                s["effective"][c] += 1
    return months


# ------------------------------------------------------------ correcciones --

def save_correction(con, message_id: int, categories: list[str] | None, note: str | None = None) -> list[str] | None:
    """Agrega una corrección humana y devuelve las categorías guardadas, en el orden de la taxonomía.
    categories=None vuelve a la clasificación automática (también es una fila más: el historial no se borra).
    KeyError si el mensaje no existe; ValueError si alguna categoría no es de la taxonomía."""
    if not con.execute("SELECT 1 FROM messages WHERE id=?", (message_id,)).fetchone():
        raise KeyError("message")
    if categories is not None:
        if any(c not in CATEGORY_KEYS for c in categories):
            raise ValueError("Categorías inválidas.")
        categories = [c for c in CATEGORY_KEYS if c in categories]
    con.execute("INSERT INTO human_labels(message_id, categories, note, created_at) VALUES (?,?,?,?)",
                (message_id, json.dumps(categories) if categories is not None else None, (note or "")[:500] or None,
                 datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")))
    return categories


def revert_correction(con, message_id: int) -> None:
    save_correction(con, message_id, None)
