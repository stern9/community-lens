"""Qué es una edición guardada del periódico. Es el único lugar que conoce sus campos.

Una edición se guarda como JSON (editions.data) y se dibuja de nuevo cada vez que se abre, así una mejora de la
plantilla llega también a las ediciones viejas; lo que se publica de una aprobada, en cambio, son los bytes de su
versión pública (newspaper_html.render con public: sin lecturas, sin "En números" ni nombre del grupo) dibujados
al aprobarla (ver "Ciclo de vida"). Quien la arma (newspaper.py) usa build(); quien la
dibuja (newspaper_html.py) recibe siempre una Edition completa, porque todo lo que sale de la base pasa por
upgrade(), que lleva cualquier forma anterior a la actual. Para agregar un campo: declararlo acá, llenarlo en
build(), darle su valor por defecto en upgrade() y subir FORMAT.

Formatos:
  1  sin el campo "format". Según cuándo se generó puede no traer algunos campos opcionales (hilo, antetítulo,
     nota, cuenta, video, vista previa…) ni sent.pages_tried.
  2  todos los campos presentes, con "format": 2.
  3  agrega "others": los enlaces que pasaron del tope, sin resumen. Las de antes no los guardaron: lista vacía.
  4  cada uno de "others" agrega "caption" y "account" (lo que la plataforma publica de un post). En las de antes, None.

La columna editions.html ya no se usa: las ediciones nuevas la dejan vacía y nunca se lee (las viejas conservan
lo que tenían; no se borra nada).

Ciclo de vida (editions.status, fuera de data: es de la fila, no del contenido, y no cambia FORMAT):
  borrador    toda edición recién generada (save). No se publica; su página lleva la banda de borrador.
  aprobada    approve() dibujó una vez su versión pública, sin banda, y guardó esos bytes (approved_html) con su
              sha256 y quién, por qué canal, cuándo y adónde (approval, con "public": true). Se publican esos bytes
              tal cual, solo si blocked_reason() no los frena: hash roto, aprobada antes de la versión pública (sin
              "public") o bytes con lecturas de la conversación.
  descartada  oculta y fuera del sitio; restore() la vuelve a borrador sin tocar approval ni approved_html.
  heredada    guardada antes de que existiera la aprobación: se publica dibujando su versión pública, sin hash. Ningún
              INSERT nuevo la produce (es el valor por defecto de la columna para las filas viejas).
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TypedDict

from . import dates, newspaper_html

FORMAT = 4


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


class OtherLink(TypedDict):
    """Un enlace que pasó del tope de su tipo: va en la lista de otros enlaces, sin modelo."""
    kind: str                # article | social | tool
    url: str                 # siempre un enlace del chat
    shared: str
    domain: str
    platform: str | None     # solo publicaciones en redes
    title: str | None        # <title> de la página leída; None: no se leyó o no se pudo, se muestra el dominio
    published: str | None    # AAAA-MM-DD, solo si la página declara article:published_time
    caption: str | None      # solo publicaciones: texto del post o título del video, como lo publica la plataforma
    account: str | None      # solo publicaciones: cuenta o canal


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
    "timeline": list[TimelineEntry], "unread": list[Unread], "others": list[OtherLink]})


class Sent(TypedDict):
    """Qué salió de la máquina al generar la edición, y hacia dónde."""
    pages_to: str
    pages_tried: list[str]   # modelos que recibieron el texto de las páginas y fallaron antes del que redactó
    pages: int
    chat_messages: int
    chat_to: list[str]


class Approval(TypedDict):
    """Quién aprobó una edición, cuándo, por qué canal y adónde, y el sha256 de los bytes que se publican."""
    hash: str                # "sha256:<hex>" de approved_html
    by: str
    via: str
    at: str                  # instante UTC, AAAA-MM-DDTHH:MM:SSZ
    to: str | None
    public: bool             # False: aprobada antes de la versión pública; sus bytes son la página completa


STATUSES = ("borrador", "aprobada", "descartada", "heredada")
PUBLISHED = ("aprobada", "heredada")  # los estados que entran al sitio
WHO_MAX = 200                         # caracteres de "por quién" y "por qué canal"
TO_MAX = 500                          # caracteres de "adónde" (puede ser una ruta)


def build(chat_name: str, d_from: str, d_to: str, facts: Facts, editor: Editor, readings: list[Reading], body: Body,
          others: list[OtherLink]) -> Edition:
    return {"format": FORMAT, "chat_name": chat_name, "from": d_from, "to": d_to, "range_label": dates.range_label(d_from, d_to),
            "created_label": dates.long_day(datetime.now().astimezone()), "facts": facts, "editor": editor, "readings": readings,
            "thread": body["thread"], "lead": body["lead"], "articles": body["articles"], "socials": body["socials"],
            "tools": body["tools"], "timeline": body["timeline"], "unread": body["unread"], "others": others}


def _article(a: dict) -> Article:
    return {"id": a.get("id", 0), "url": a["url"], "shared": a.get("shared", ""), "kicker": a.get("kicker") or "",
            "headline": a["headline"], "outlet": a.get("outlet") or "", "byline": a.get("byline"), "published": a.get("published"),
            "summary": a.get("summary") or [], "note": a.get("note")}


def upgrade(d: dict) -> Edition:
    """Lleva una edición guardada con cualquier formato anterior a la forma actual. No toca la base."""
    fmt = d.get("format", 1)
    if fmt == FORMAT:
        return d  # type: ignore[return-value]
    if fmt not in (1, 2, 3):
        raise ValueError(f"Edición con un formato que esta versión no conoce ({d.get('format')}).")
    if fmt == 1:
        d = _from_v1(d)
    others = [{**x, "caption": x.get("caption"), "account": x.get("account")} for x in d.get("others") or []]
    return {**d, "format": FORMAT, "others": others}  # type: ignore[typeddict-item]


def _from_v1(d: dict) -> dict:
    """Formato 1 (sin "format"): completa los campos opcionales que todavía no existían."""
    f = d.get("facts") or {}
    return {
        "chat_name": d["chat_name"], "from": d["from"], "to": d["to"],
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
        "others": [],
    }


def upgrade_sent(s: dict) -> Sent:
    return {"pages_to": s["pages_to"], "pages_tried": s.get("pages_tried") or [], "pages": s["pages"],
            "chat_messages": s.get("chat_messages", 0), "chat_to": s.get("chat_to") or []}


def upgrade_approval(raw: str) -> Approval:
    """El registro de aprobación guardado (texto JSON). Si alguien lo dejó incompleto o ilegible por SQL, los campos
    que faltan salen vacíos: sin hash, hash_ok() la da por dañada, y la lista se sigue mostrando. Sin "public" (las
    aprobadas antes de la versión pública) es False: blocked_reason() la deja fuera del sitio."""
    try:
        a = json.loads(raw)
    except ValueError:
        a = None
    a = a if isinstance(a, dict) else {}
    return {"hash": str(a.get("hash") or ""), "by": str(a.get("by") or ""), "via": str(a.get("via") or ""),
            "at": str(a.get("at") or ""), "to": str(a["to"]) if a.get("to") else None,
            "public": a.get("public") is True}


def page_hash(page: bytes) -> str:
    return "sha256:" + hashlib.sha256(page).hexdigest()


def approved_bytes(row) -> bytes | None:
    """approved_html de una fila. Se guarda como BLOB; si alguien lo cambió por SQL a texto, sus bytes UTF-8."""
    v = row["approved_html"]
    return v.encode("utf-8") if isinstance(v, str) else v


def hash_ok(row) -> bool | None:
    """Si los bytes aprobados siguen siendo los que se aprobaron. None si la fila no está aprobada."""
    if row["status"] != "aprobada":
        return None
    page = approved_bytes(row)
    return bool(row["approval"]) and page is not None and upgrade_approval(row["approval"])["hash"] == page_hash(page)


def blocked_reason(row) -> str | None:
    """Por qué una aprobada no entra al sitio, o None si entra (o si no es aprobada: el resto lo decide su estado).
    Un texto corto para personas: publish() lo pone en "skipped" y listing() en la fila, que muestran --listar y la
    pantalla tal cual."""
    if row["status"] != "aprobada":
        return None
    if not hash_ok(row):
        return "dañada: no coincide con su hash; reaprobala para publicarla"
    if not upgrade_approval(row["approval"])["public"]:
        return "aprobada antes de la versión pública: reaprobala para publicarla"
    if newspaper_html.READINGS_MARKER in approved_bytes(row):  # segunda defensa, aunque diga public
        return "contiene lecturas de la conversación: reaprobala para publicarla"
    return None


def leaks_readings(row) -> bool | None:
    """Si los bytes aprobados llevan lecturas de la conversación. None si la fila no está aprobada o no tiene bytes."""
    page = approved_bytes(row) if row["status"] == "aprobada" else None
    return None if page is None else newspaper_html.READINGS_MARKER in page


# ------------------------------------------------------------------- base --

def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def save(con, chat_id: int, ed: Edition, sent: Sent) -> int:
    """Guarda una edición recién generada. Siempre como borrador: entra al sitio solo cuando alguien la aprueba."""
    return con.execute("""INSERT INTO editions(chat_id, date_from, date_to, created_at, editor, sent, data, html, status)
                          VALUES (?,?,?,?,?,?,?,'','borrador')""",
                       (chat_id, ed["from"], ed["to"], _now(),
                        json.dumps(ed["editor"]), json.dumps(sent), json.dumps(ed, ensure_ascii=False))).lastrowid


def load(con, edition_id: int) -> Edition | None:
    r = con.execute("SELECT data FROM editions WHERE id=?", (edition_id,)).fetchone()
    return upgrade(json.loads(r["data"])) if r else None


def load_row(con, edition_id: int):
    """(fila, edición) de una edición guardada, o None si no existe. La fila trae el estado y la aprobación;
    la edición ya pasó por upgrade() (ValueError si la guardó una versión más nueva de la app)."""
    r = con.execute("SELECT * FROM editions WHERE id=?", (edition_id,)).fetchone()
    return (r, upgrade(json.loads(r["data"]))) if r else None


def _up_to_date(r) -> bool | None:
    """Si los bytes aprobados son la versión pública que dibujaría hoy la plantilla. Solo informa: lo publicado no
    cambia hasta reaprobar."""
    if r["status"] != "aprobada" or r["approved_html"] is None:
        return None
    try:
        return approved_bytes(r) == newspaper_html.render(upgrade(json.loads(r["data"])), public=True).encode("utf-8")
    except ValueError:  # guardada por una versión más nueva: esta no sabe dibujarla, así que no puede compararla
        return None


def listing(con, chat_id: int) -> list[dict]:
    """Las ediciones de un grupo, la más reciente primero, sin su contenido ni los bytes aprobados. in_site dice si
    publish() la escribiría ahora: la publicable más reciente de su rango, y si es aprobada, sin blocked_reason(), que
    va en la fila como el motivo para mostrar. leaks_readings dice si los bytes aprobados llevan lecturas (None si no es
    aprobada)."""
    site = {r["id"] for r in publishable(con, chat_id) if blocked_reason(r) is None}
    return [{"id": r["id"], "from": r["date_from"], "to": r["date_to"], "created_at": r["created_at"],
             "editor": json.loads(r["editor"]), "sent": upgrade_sent(json.loads(r["sent"])), "status": r["status"],
             "approval": upgrade_approval(r["approval"]) if r["approval"] else None,
             "hash_ok": hash_ok(r), "up_to_date": _up_to_date(r), "leaks_readings": leaks_readings(r),
             "blocked_reason": blocked_reason(r), "in_site": r["id"] in site}
            for r in con.execute("""SELECT id, date_from, date_to, created_at, editor, sent, status, approval, approved_html, data
                                    FROM editions WHERE chat_id=? ORDER BY id DESC""", (chat_id,))]


def listed(con, edition_id: int) -> dict | None:
    """Una edición con la misma forma que una fila de listing(), o None si no existe."""
    r = con.execute("SELECT chat_id FROM editions WHERE id=?", (edition_id,)).fetchone()
    return next((x for x in listing(con, r["chat_id"]) if x["id"] == edition_id), None) if r else None


def _text(v: str | None, what: str, limit: int, required: bool = True) -> str | None:
    v = (v or "").strip()
    if not v:
        if required:
            raise ValueError(f"Falta {what}.")
        return None
    if len(v) > limit:
        raise ValueError(f"{what[0].upper()}{what[1:]} puede tener hasta {limit} caracteres.")
    return v


def check_approval(by: str | None, via: str | None, to: str | None = None) -> tuple[str, str, str | None]:
    """Quién aprueba, por qué canal y adónde, recortados; el destino vacío es None. ValueError si falta quién o el
    canal, o si alguno pasa de su largo. La usan approve() y el comando, que la corre antes de abrir la base."""
    return (_text(by, "quién aprueba", WHO_MAX), _text(via, "el canal por el que se aprobó", WHO_MAX),
            _text(to, "el destino", TO_MAX, required=False))


def approve(con, edition_id: int, by: str, via: str, to: str | None = None) -> Approval:
    """Aprueba una edición (borrador, heredada o aprobada otra vez): dibuja una sola vez su versión pública, sin
    banda, y guarda esos bytes, su sha256, quién, por qué canal, cuándo y adónde, con "public": true. Reaprobar reemplaza bytes y registro (no hay historial).
    ValueError si los datos no sirven, si no existe o si está descartada (primero se recupera)."""
    by, via, to = check_approval(by, via, to)
    found = load_row(con, edition_id)
    if not found:
        raise ValueError(f"No existe la edición {edition_id}.")
    row, ed = found
    if row["status"] == "descartada":
        raise ValueError(f"La edición {edition_id} está descartada: recuperala antes de aprobarla.")
    page = newspaper_html.render(ed, public=True).encode("utf-8")
    approval: Approval = {"hash": page_hash(page), "by": by, "via": via, "at": _now(), "to": to, "public": True}
    con.execute("UPDATE editions SET status='aprobada', approval=?, approved_html=? WHERE id=?",
                (json.dumps(approval, ensure_ascii=False), page, edition_id))
    return approval


def _move(con, edition_id: int, to_status: str, allowed: tuple[str, ...], why: str) -> None:
    r = con.execute("SELECT status FROM editions WHERE id=?", (edition_id,)).fetchone()
    if not r:
        raise ValueError(f"No existe la edición {edition_id}.")
    if r["status"] not in allowed:
        raise ValueError(f"La edición {edition_id} {why}.")
    con.execute("UPDATE editions SET status=? WHERE id=?", (to_status, edition_id))


def discard(con, edition_id: int) -> None:
    """Oculta una edición y la saca del sitio en la próxima publicación. Se puede recuperar; no borra nada."""
    _move(con, edition_id, "descartada", ("borrador", "aprobada", "heredada"), "ya está descartada")


def restore(con, edition_id: int) -> None:
    """Una descartada vuelve a borrador: hay que aprobarla de nuevo para que entre al sitio."""
    _move(con, edition_id, "borrador", ("descartada",), "no está descartada")


def last_covered_day(con, chat_id: int) -> str | None:
    """Último día que cubre alguna edición aprobada o heredada del grupo; None si no tiene. Un borrador no cuenta
    (todavía no se publicó) y una descartada deja de contar: si era la que llegaba más lejos, su período se vuelve a cubrir."""
    return con.execute("SELECT MAX(date_to) d FROM editions WHERE chat_id=? AND status IN ('aprobada','heredada')",
                       (chat_id,)).fetchone()["d"]


def publishable(con, chat_id: int) -> list:
    """Lo que se publica: la edición más reciente de cada rango entre las aprobadas y las heredadas, del rango más
    nuevo al más viejo. Filas con id, date_from, date_to, status, approval, approved_html y data; quien publica
    comprueba las aprobadas con blocked_reason(). Una aprobada dañada no cede su rango a otra más vieja."""
    latest: dict[tuple[str, str], object] = {}
    for r in con.execute("""SELECT id, date_from, date_to, status, approval, approved_html, data FROM editions
                            WHERE chat_id=? AND status IN ('aprobada','heredada') ORDER BY id""", (chat_id,)):
        latest[(r["date_from"], r["date_to"])] = r
    return [r for _, r in sorted(latest.items(), key=lambda kv: (kv[0][1], kv[0][0]), reverse=True)]
