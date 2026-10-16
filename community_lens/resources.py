"""Biblioteca de recursos: documentos y enlaces compartidos en un chat.

Responde "¿dónde está ese archivo y cuál es la última versión?" sin leer el historial.
Es determinista: agrupa por nombre de archivo tal como figura en la exportación, o por la identidad del enlace.
El archivo en sí no está en el TXT; aquí solo consta que se compartió, cuándo y por quién.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

_EXT = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|md|txt|csv|zip|key|pages|numbers|json|html?|epub|png|jpe?g)$", re.I)
_COPY = re.compile(r"\s*\(\d+\)$")                       # "informe (1).pdf": copia descargada
_VERSION = re.compile(r"(?:^|[-_ .])v(?:er(?:si[oó]n)?)?[-_ .]?(\d+(?:[._]\d+)*)$", re.I)
_SEP = re.compile(r"[-_\s]+")


# ---------------------------------------------------------------- enlaces --
# Qué es "el mismo enlace" se define acá y en ningún otro lado. Recursos, el periódico y las páginas
# guardadas (tabla pages) agrupan por link_key() y muestran clean_url().
# Parámetros de seguimiento: no cambian la página y varios identifican a quien compartió el enlace (igsh, si, rcm, xmt,
# u_code…), así que no se muestran ni se publican. Los nombres van en minúsculas; se comparan sin distinguirlas.
_TRACKING_PREFIX = "utm_"
_TRACKING = {"fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "twclid", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
             "mkt_tok", "igsh", "igshid"}
# Los que solo son de seguimiento en ciertos sitios: en otro sitio, "si" o "t" pueden ser parte de la dirección.
_TRACKING_BY_SITE = {
    ("youtube.com", "youtu.be", "spotify.com", "spotify.link"): {"si", "feature", "pp"},
    ("x.com", "twitter.com"): {"s", "t", "ref_src", "ref_url"},
    ("facebook.com", "fb.watch"): {"mibextid", "rdid", "__cft__[0]", "__cft__%5b0%5d", "__tn__", "sfnsn"},
    ("tiktok.com",): {"_r", "_t", "is_from_webapp", "sender_device", "web_id", "share_app_id", "share_item_id", "share_link_id",
                      "tt_from", "u_code", "user_id", "checksum", "sec_user_id", "social_share_type", "timestamp"},
    ("linkedin.com", "lnkd.in"): {"rcm", "trk", "trackingid", "lipi", "midtoken", "midsig", "trkemail", "originalsubdomain"},
    ("threads.net", "threads.com"): {"xmt", "slof"},
}
_HOST_PREFIXES = ("www.", "m.", "mobile.")


def _is_tracking(name: str, host: str) -> bool:
    name = name.lower()
    if name.startswith(_TRACKING_PREFIX) or name in _TRACKING:
        return True
    host = host.lower()
    return any(name in names for sites, names in _TRACKING_BY_SITE.items() if any(host == s or host.endswith("." + s) for s in sites))


def _without_tracking(query: str, host: str) -> str:
    return "&".join(part for part in query.split("&") if part and not _is_tracking(part.partition("=")[0], host))


def clean_url(url: str) -> str:
    """La URL tal como se compartió, sin parámetros de seguimiento (en cualquier posición). Es la que se muestra, se abre y
    se publica."""
    head, sep, fragment = url.partition("#")
    base, mark, query = head.partition("?")
    if not mark:
        return url
    try:
        host = urlsplit(base if "://" in base else "//" + base).hostname or ""
    except ValueError:
        host = ""
    query = _without_tracking(query, host)
    return base + ("?" + query if query else "") + sep + fragment


def link_key(url: str) -> str:
    """Identidad de un enlace: dos URLs con la misma clave son el mismo enlace.
    No distingue mayúsculas en el dominio, prefijos www./m./mobile., puerto por defecto, barra final ni parámetros de
    seguimiento.
    Es una clave para agrupar, no una dirección: no se abre ni se muestra."""
    try:
        parts = urlsplit(url if "://" in url else "//" + url)
        host, port = parts.hostname or "", parts.port
    except ValueError:  # dirección mal formada: se compara tal cual
        return url.rstrip("/")
    for prefix in _HOST_PREFIXES:
        if host.startswith(prefix) and "." in host[len(prefix):]:
            host = host[len(prefix):]
    if (parts.scheme.lower(), port) in (("https", 443), ("http", 80)):
        port = None
    query = _without_tracking(parts.query, host)
    return ((parts.scheme.lower() + "://" if parts.scheme else "") + host + (f":{port}" if port else "") + parts.path.rstrip("/")
            + ("?" + query if query else "") + ("#" + parts.fragment if parts.fragment else ""))


def split_name(name: str) -> tuple[str, str, tuple[int, ...] | None]:
    """'Guia-Demo-Manual-v1_6.pdf' → ('guia demo manual', 'Guia-Demo-Manual', (1, 6))."""
    stem = _COPY.sub("", _EXT.sub("", name.strip()))
    version = None
    m = _VERSION.search(stem)
    if m and m.start() > 0:
        version = tuple(int(x) for x in re.split(r"[._]", m.group(1)))
        stem = stem[: m.start()]
    title = stem.strip(" -_.") or name.strip()
    return _SEP.sub(" ", title).casefold().strip(), title, version


def _where(chat_id: int, d_from: str | None, d_to: str | None) -> tuple[str, list]:
    w, p = ["m.chat_id = ?", "m.record_type = 'message'"], [chat_id]
    if d_from:
        w.append("substr(m.ts_local,1,10) >= ?"); p.append(d_from)
    if d_to:
        w.append("substr(m.ts_local,1,10) <= ?"); p.append(d_to)
    return " AND ".join(w), p


def documents(con, chat_id: int, d_from: str | None = None, d_to: str | None = None) -> tuple[list[dict], int]:
    where, p = _where(chat_id, d_from, d_to)
    rows = con.execute(
        f"""SELECT m.id, m.ts_local, m.content_text, COALESCE(ca.display_label, a.display_label, m.author_raw) author
            FROM messages m LEFT JOIN authors a ON a.id = m.author_id LEFT JOIN authors ca ON ca.id = a.canonical_id
            WHERE {where} AND m.media_kind = 'document' ORDER BY m.seq""", p).fetchall()
    groups: dict[str, dict] = {}
    unnamed = 0
    for r in rows:
        name = (r["content_text"] or "").split("\n", 1)[0].strip()
        if not name:
            unnamed += 1  # el exportador no conservó el nombre del archivo
            continue
        key, title, version = split_name(name)
        g = groups.setdefault(key, {"title": title, "shares": []})
        g["shares"].append({"message_id": r["id"], "name": name, "ts_local": r["ts_local"], "author": r["author"],
                            "version": ".".join(map(str, version)) if version else None, "_v": version or ()})
    out = []
    for key, g in groups.items():
        shares = g["shares"]
        # Una "versión" = un nombre de archivo distinto; se queda con su envío más reciente.
        by_name: dict[str, dict] = {}
        for s in shares:
            cur = by_name.get(s["name"])
            by_name[s["name"]] = {**s, "times_shared": (cur["times_shared"] if cur else 0) + 1,
                                  "first_shared": cur["first_shared"] if cur else s["ts_local"]}
        versions = sorted(by_name.values(), key=lambda s: (s["_v"], s["ts_local"]), reverse=True)
        latest = versions[0]
        newest_share = max(shares, key=lambda s: s["ts_local"])
        out.append({
            "kind": "document", "key": key, "title": g["title"],
            "shares": len(shares), "first_shared": min(s["ts_local"] for s in shares), "last_shared": newest_share["ts_local"],
            "sharers": sorted({s["author"] for s in shares if s["author"]}),
            "latest": _pub(latest),
            "versions": [_pub(v) for v in versions],
            "has_versions": any(v["_v"] for v in versions),
            # Si lo último que se envió no es la versión más alta, alguien reenvió una versión vieja.
            "latest_is_most_recent": newest_share["name"] == latest["name"],
        })
    return out, unnamed


def _pub(s: dict) -> dict:
    return {"message_id": s["message_id"], "name": s["name"], "version": s["version"], "ts_local": s["ts_local"],
            "author": s["author"], "times_shared": s["times_shared"], "first_shared": s["first_shared"]}


def links(con, chat_id: int, d_from: str | None = None, d_to: str | None = None) -> list[dict]:
    where, p = _where(chat_id, d_from, d_to)
    rows = con.execute(
        f"""SELECT l.url, l.domain, m.id, m.ts_local, COALESCE(ca.display_label, a.display_label, m.author_raw) author
            FROM links l JOIN messages m ON m.id = l.message_id
            LEFT JOIN authors a ON a.id = m.author_id LEFT JOIN authors ca ON ca.id = a.canonical_id
            WHERE {where} ORDER BY m.seq""", p).fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        key = link_key(r["url"])
        g = groups.setdefault(key, {"kind": "link", "key": key, "url": clean_url(r["url"]), "domain": r["domain"], "messages": set(),
                                    "first_shared": r["ts_local"], "sharers": set()})
        g["messages"].add(r["id"])
        g["last_shared"], g["message_id"], g["author"] = r["ts_local"], r["id"], r["author"]
        if r["author"]:
            g["sharers"].add(r["author"])
    return [{**{k: v for k, v in g.items() if k != "messages"}, "shares": len(g["messages"]), "sharers": sorted(g["sharers"])}
            for g in groups.values()]


def library(con, chat_id: int, *, kind: str = "document", q: str | None = None, order: str = "recent",
            d_from: str | None = None, d_to: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    docs, unnamed = documents(con, chat_id, d_from, d_to)
    lks = links(con, chat_id, d_from, d_to)
    items = docs if kind == "document" else lks
    needle = (q or "").casefold().strip()
    if needle:
        words = needle.split()

        def hay(it: dict) -> str:
            if it["kind"] == "document":
                return " ".join([it["title"], it["key"]] + [v["name"] for v in it["versions"]] + it["sharers"]).casefold()
            return " ".join([it["url"], it["domain"]] + it["sharers"]).casefold()
        items = [it for it in items if all(w in hay(it) for w in words)]
    if order == "shared":
        items.sort(key=lambda it: (it["shares"], it["last_shared"]), reverse=True)
    else:
        items.sort(key=lambda it: it["last_shared"], reverse=True)
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    return {
        "kind": kind, "total": len(items), "limit": limit, "offset": offset, "items": items[offset: offset + limit],
        "counts": {"documents": len(docs), "document_shares": sum(d["shares"] for d in docs), "unnamed_documents": unnamed,
                   "links": len(lks), "link_shares": sum(l["shares"] for l in lks)},
        "note": "Los archivos no están en la exportación: aquí consta que se compartieron, cuándo y por quién. La «última "
                "versión» es el número de versión más alto que aparece en el nombre del archivo dentro de este chat; no "
                "se puede saber si existe una más nueva compartida en otro lugar, como enlace o con otro nombre.",
    }
