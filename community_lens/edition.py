"""Qué es una edición guardada del periódico. Es el único lugar que conoce sus campos.

Una edición se guarda como JSON (editions.data) y se dibuja de nuevo cada vez que se abre o se publica, así una
mejora de la plantilla llega también a las ediciones viejas. Quien la arma (newspaper.py) usa build(); quien la
dibuja (newspaper_html.py) recibe siempre una Edition completa, porque todo lo que sale de la base pasa por
upgrade(), que lleva cualquier forma anterior a la actual. Para agregar un campo: declararlo acá, llenarlo en
build(), darle su valor por defecto en upgrade() y subir FORMAT.

Formatos:
  1  sin el campo "format". Según cuándo se generó puede no traer algunos campos opcionales (hilo, antetítulo,
     nota, cuenta, video, vista previa…) ni sent.pages_tried.
  2  todos los campos presentes, con "format": 2.

La columna editions.html ya no se usa: las ediciones nuevas la dejan vacía y nunca se lee (las viejas conservan
lo que tenían; no se borra nada).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TypedDict

from . import dates

FORMAT = 2


class Editor(TypedDict):
    name: str            # anthropic | openai | gemini
    label: str
    model: str


class Facts(TypedDict):  # calculados por código sobre el chat
    messages: int
    participants: int
    messages_with_links: int
    busiest_day: str | None
    busiest_day_messages: int


class Article(TypedDict):
    id: int
    url: str             # siempre un enlace del chat
    shared: str          # AAAA-MM-DD
    kicker: str
    headline: str
    outlet: str
    byline: str | None
    published: str | None
    summary: list[str]
    note: str | None


class Social(TypedDict):
    url: str
    shared: str
    platform: str
    account: str | None
    text: str
    video: bool
    preview: bool        # False: la plataforma no entregó contenido público


class Tool(TypedDict):
    url: str
    shared: str
    name: str
    text: str
    preview: bool


class TimelineEntry(TypedDict):
    date: str
    text: str
    source: str


class Unread(TypedDict):
    url: str
    domain: str
    shared: str
    reason: str


class Topic(TypedDict):
    name: str
    messages: int        # contados por código
    participants: int


class Reading(TypedDict):
    provider: str
    label: str
    model: str
    paragraphs: list[str]
    topics: list[Topic]


class Body(TypedDict):
    """Lo que redacta el modelo, ya validado y unido a los enlaces reales (newspaper.assemble)."""
    thread: str | None
    lead: Article
    articles: list[Article]
    socials: list[Social]
    tools: list[Tool]
    timeline: list[TimelineEntry]
    unread: list[Unread]


Edition = TypedDict("Edition", {
    "format": int, "chat_name": str, "from": str, "to": str, "range_label": str, "created_label": str,
    "facts": Facts, "editor": Editor, "readings": list[Reading],
    "thread": str | None, "lead": Article, "articles": list[Article], "socials": list[Social], "tools": list[Tool],
    "timeline": list[TimelineEntry], "unread": list[Unread]})


class Sent(TypedDict):
    """Qué salió de la máquina al generar la edición, y hacia dónde."""
    pages_to: str
    pages_tried: list[str]   # modelos que recibieron el texto de las páginas y fallaron antes del que redactó
    pages: int
    chat_messages: int
    chat_to: list[str]


def build(chat_name: str, d_from: str, d_to: str, facts: Facts, editor: Editor, readings: list[Reading], body: Body) -> Edition:
    return {"format": FORMAT, "chat_name": chat_name, "from": d_from, "to": d_to, "range_label": dates.range_label(d_from, d_to),
            "created_label": dates.long_day(datetime.now().astimezone()), "facts": facts, "editor": editor, "readings": readings,
            "thread": body["thread"], "lead": body["lead"], "articles": body["articles"], "socials": body["socials"],
            "tools": body["tools"], "timeline": body["timeline"], "unread": body["unread"]}


def _article(a: dict) -> Article:
    return {"id": a.get("id", 0), "url": a["url"], "shared": a.get("shared", ""), "kicker": a.get("kicker") or "",
            "headline": a["headline"], "outlet": a.get("outlet") or "", "byline": a.get("byline"), "published": a.get("published"),
            "summary": a.get("summary") or [], "note": a.get("note")}


def upgrade(d: dict) -> Edition:
    """Lleva una edición guardada con cualquier formato anterior a la forma actual. No toca la base."""
    if d.get("format") == FORMAT:
        return d  # type: ignore[return-value]
    if d.get("format", 1) != 1:
        raise ValueError(f"Edición con un formato que esta versión no conoce ({d.get('format')}).")
    f = d.get("facts") or {}
    return {
        "format": FORMAT, "chat_name": d["chat_name"], "from": d["from"], "to": d["to"],
        "range_label": d.get("range_label") or dates.range_label(d["from"], d["to"]), "created_label": d.get("created_label") or "",
        "facts": {"messages": f.get("messages", 0), "participants": f.get("participants", 0),
                  "messages_with_links": f.get("messages_with_links", 0), "busiest_day": f.get("busiest_day"),
                  "busiest_day_messages": f.get("busiest_day_messages", 0)},
        "editor": d["editor"],
        "readings": [{"provider": r.get("provider", ""), "label": r["label"], "model": r["model"], "paragraphs": r.get("paragraphs") or [],
                      "topics": r.get("topics") or []} for r in d.get("readings") or []],
        "thread": d.get("thread"), "lead": _article(d["lead"]), "articles": [_article(a) for a in d.get("articles") or []],
        "socials": [{"url": s["url"], "shared": s.get("shared", ""), "platform": s["platform"], "account": s.get("account"),
                     "text": s.get("text") or "", "video": bool(s.get("video", False)), "preview": bool(s.get("preview", True))}
                    for s in d.get("socials") or []],
        "tools": [{"url": t["url"], "shared": t.get("shared", ""), "name": t["name"], "text": t.get("text") or "",
                   "preview": bool(t.get("preview", True))} for t in d.get("tools") or []],
        "timeline": [{"date": t["date"], "text": t["text"], "source": t.get("source") or ""} for t in d.get("timeline") or []],
        "unread": [{"url": u["url"], "domain": u["domain"], "shared": u.get("shared", ""), "reason": u.get("reason") or ""}
                   for u in d.get("unread") or []],
    }


def upgrade_sent(s: dict) -> Sent:
    return {"pages_to": s["pages_to"], "pages_tried": s.get("pages_tried") or [], "pages": s["pages"],
            "chat_messages": s.get("chat_messages", 0), "chat_to": s.get("chat_to") or []}


# ------------------------------------------------------------------- base --

def save(con, chat_id: int, ed: Edition, sent: Sent) -> int:
    return con.execute("""INSERT INTO editions(chat_id, date_from, date_to, created_at, editor, sent, data, html)
                          VALUES (?,?,?,?,?,?,?,'')""",
                       (chat_id, ed["from"], ed["to"], datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        json.dumps(ed["editor"]), json.dumps(sent), json.dumps(ed, ensure_ascii=False))).lastrowid


def load(con, edition_id: int) -> Edition | None:
    r = con.execute("SELECT data FROM editions WHERE id=?", (edition_id,)).fetchone()
    return upgrade(json.loads(r["data"])) if r else None


def listing(con, chat_id: int) -> list[dict]:
    """Las ediciones de un grupo, la más reciente primero, sin su contenido."""
    return [{"id": r["id"], "from": r["date_from"], "to": r["date_to"], "created_at": r["created_at"],
             "editor": json.loads(r["editor"]), "sent": upgrade_sent(json.loads(r["sent"]))}
            for r in con.execute("SELECT id, date_from, date_to, created_at, editor, sent FROM editions WHERE chat_id=? ORDER BY id DESC",
                                 (chat_id,))]


def latest_per_range(con, chat_id: int) -> list[Edition]:
    """La edición más reciente de cada rango, del rango más nuevo al más viejo: lo que se publica."""
    latest: dict[tuple[str, str], str] = {}
    for r in con.execute("SELECT date_from, date_to, data FROM editions WHERE chat_id=? ORDER BY id", (chat_id,)):
        latest[(r["date_from"], r["date_to"])] = r["data"]
    return [upgrade(json.loads(data)) for _, data in sorted(latest.items(), key=lambda kv: (kv[0][1], kv[0][0]), reverse=True)]
