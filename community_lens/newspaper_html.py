"""Plantilla fija del periódico: un HTML autocontenido, sin scripts ni recursos externos.

Todo texto (del modelo o de las páginas) entra escapado; las direcciones salen de la base, no del modelo.
Recibe una edición con la forma actual (edition.Edition): las guardadas con una forma anterior ya pasaron
por edition.upgrade(), así que acá no hay valores por defecto.
"""
from __future__ import annotations

from html import escape as e
from pathlib import Path

from .dates import fmt_day, short_day
from .edition import Article, Edition, Reading

CSS = Path(__file__).with_name("newspaper.css").read_text(encoding="utf-8")
ICON = {"X": "𝕏", "YouTube": "▶️", "TikTok": "🎵", "Instagram": "📸", "LinkedIn": "💼", "Facebook": "📘", "Threads": "🧵",
        "Reddit": "👽", "Bluesky": "🦋"}


def _link(url: str, text: str, cls: str = "fp-a") -> str:
    return f'<a class="{cls}" href="{e(url, quote=True)}" target="_blank" rel="noopener noreferrer">{e(text)}</a>'


def _shared(day: str) -> str:
    return f'<span class="fp-shared">Compartido {e(short_day(day))}</span>'


def _meta(a: Article, sep: str = "<br>") -> str:
    by = f" · {e(a['byline'])}" if a["byline"] else ""
    pub = f"{sep}Publicado: {e(a['published'])}" if a["published"] else ""
    return f'<p class="fp-meta">{_shared(a["shared"])}<b>{e(a["outlet"])}</b>{by}{pub}</p>'


def _article(a: Article) -> str:
    note = f'<p class="fp-note">{e(a["note"])}</p>' if a["note"] else ""
    kicker = f'<p class="fp-kicker">{e(a["kicker"])}</p>' if a["kicker"] else ""
    return (f'<article class="fp-art">{kicker}<h3>{_link(a["url"], a["headline"])}</h3>{_meta(a)}'
            + "".join(f"<p>{e(s)}</p>" for s in a["summary"]) + note
            + _link(a["url"], "Leer el artículo original →", "fp-more") + "</article>")


def _card(tag: str, who: str, text: str, shared: str, url: str, cta: str, *, video=False, preview=True, tool=False, anchor="") -> str:
    video_tag = '<span class="fp-tag">video</span>' if video else ""
    nop = "" if preview else '<span class="fp-nopreview">sin vista previa disponible</span>'
    return (f'<div class="fp-card{" fp-card-tool" if tool else ""}"{anchor}><div class="fp-item-h">{tag}{video_tag}</div>'
            f'<div class="fp-card-who">{e(who)}</div><p>{e(text)}</p>{nop}'
            f'<div class="fp-card-foot">{_shared(shared)}{_link(url, cta, "fp-more")}</div></div>')


def _reading(r: Reading) -> str:
    topics = "".join(f"<li><span>{e(t['name'])}</span><b>{t['messages']}</b></li>" for t in r["topics"])
    topics_box = (f'<div class="fp-view-topics"><div class="fp-view-cap">Temas según {e(r["label"])} · mensajes contados por código</div>'
                  f"<ul>{topics}</ul></div>") if topics else ""
    return (f'<section class="fp-box fp-view"><h2>La visión de {e(r["label"])}</h2>'
            + "".join(f"<p>{e(p)}</p>" for p in r["paragraphs"]) + topics_box
            + f'<p class="fp-view-disc">Esta es la interpretación y el entendimiento que el modelo {e(r["model"])} hizo de los '
              "mensajes de este período.</p></section>")


def render(d: Edition) -> str:
    lead, arts, socials, tools, readings, f = d["lead"], d["articles"], d["socials"], d["tools"], d["readings"], d["facts"]
    plural = lambda n, one, many: f"{n} {one if n == 1 else many}"
    nav = [f'<a href="#articulos">📰 {plural(1 + len(arts), "artículo", "artículos")}</a>']
    if socials:
        nav.append(f'<a href="#redes">💬 {plural(len(socials), "publicación en redes", "publicaciones en redes")}</a>')
    if tools:
        nav.append(f'<a href="#sitios">🧰 {plural(len(tools), "sitio o herramienta", "sitios y herramientas")}</a>')
    thread = f'<p class="fp-thread"><b>Hilo de la edición:</b> {e(d["thread"])}</p>' if d["thread"] else ""

    timeline = ""
    if d["timeline"]:
        rows = "".join(f'<li><span class="d">{e(t["date"])}</span><span>{e(t["text"])}<span class="s">{e(t["source"])}</span></span></li>'
                       for t in d["timeline"])
        timeline = f'<section class="fp-box"><h2>Cronología reciente</h2><ul class="fp-tl">{rows}</ul></section>'
    busiest = (f"; el día más activo fue el {e(fmt_day(f['busiest_day']))} ({f['busiest_day_messages']} mensajes)"
               if f["busiest_day"] else "")
    facts_line = (f"Calculado sobre el chat: {f['messages']} mensajes de {f['participants']} participantes del {e(d['range_label'])}"
                  f"{busiest}.")
    facts_box = f'<section class="fp-box"><h2>En números</h2><p class="fp-facts">{facts_line}</p></section>'
    lead_html = ('<section class="fp-lead-grid" id="articulos"><article>'
                 + (f'<p class="fp-kicker">{e(lead["kicker"])}</p>' if lead["kicker"] else "")
                 + f'<h2 class="fp-lead-h">{_link(lead["url"], lead["headline"])}</h2>{_meta(lead, " · ")}'
                 + '<div class="fp-lead-body">' + "".join(f"<p>{e(s)}</p>" for s in lead["summary"]) + "</div>"
                 + (f'<p class="fp-note">{e(lead["note"])}</p>' if lead["note"] else "")
                 + _link(lead["url"], "Leer el artículo original →", "fp-more")
                 + f'</article><aside class="fp-side">{timeline}{facts_box}</aside></section>')

    views = ""
    if readings:
        title = "Dos lecturas del período" if len(readings) == 2 else "Lecturas del período" if len(readings) > 2 else "Lectura del período"
        views = (f'<section class="fp-views" id="lecturas"><div class="fp-strip-h"><h2>{title}</h2></div>'
                 f'<div class="fp-views-row fp-n{min(len(readings), 3)}">' + "".join(_reading(r) for r in readings) + "</div></section>")

    strip = ""
    if socials or tools:
        cards = [_card(f'<span aria-hidden="true">{ICON.get(s["platform"], "🔗")}</span><span>{e(s["platform"])}</span>',
                       s["account"] or "Cuenta no disponible", s["text"], s["shared"], s["url"], f'Ver en {s["platform"]} →',
                       video=s["video"], preview=s["preview"]) for s in socials]
        cards += [_card('<span aria-hidden="true">🧰</span><span>Sitio</span>', t["name"], t["text"], t["shared"], t["url"], "Ir al sitio →",
                        preview=t["preview"], tool=True, anchor=' id="sitios"' if i == 0 else "") for i, t in enumerate(tools)]
        strip = ('<section class="fp-strip" id="redes"><div class="fp-strip-h"><h2>Redes sociales y herramientas</h2>'
                 '<span class="fp-swipe">deslizá →</span></div><div class="fp-row">' + "".join(cards) + "</div></section>")

    more = ('<h2 class="fp-section-h">— Más artículos compartidos —</h2><section class="fp-cols">'
            + "".join(_article(a) for a in arts) + "</section>") if arts else ""
    unread = ""
    if d["unread"]:
        rows = "".join(f"<li>{_link(u['url'], u['domain'])} · compartido {e(short_day(u['shared']))} · {e(u['reason'])}</li>" for u in d["unread"])
        unread = f'<section class="fp-failed"><h2>No se pudo leer</h2><ul>{rows}</ul></section>'
    editor = d["editor"]
    foot = (f"Resúmenes generados automáticamente con {e(editor['label'])} ({e(editor['model'])}) a partir de los enlaces compartidos. "
            "Pueden contener errores; consultá la fuente original. Los enlaces y las cifras salen del chat exportado. "
            "Hecho con Community Lens.")

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lo que se compartió · {e(d['chat_name'])} · {e(d['range_label'])}</title>
<style>html,body{{margin:0}}
{CSS}</style>
</head>
<body><div class="fp-root"><div class="fp-wrap">
<header>
<div class="fp-top"><span>{e(d['created_label'])}</span><span>Datos del {e(d['range_label'])}</span></div>
<h1 class="fp-title">Lo que se compartió</h1>
<p class="fp-sub">{e(d['chat_name'])}</p>
<nav class="fp-band" aria-label="Secciones">{''.join(nav)}</nav>
{thread}
</header>
{lead_html}
{views}
{strip}
{more}
{unread}
<footer class="fp-foot">{foot}</footer>
</div></div></body>
</html>
"""


def render_index(chat_name: str, entries: list[dict]) -> str:
    """Portada del sitio: todas las ediciones, la más reciente primero."""
    rows = "".join(
        f'<article class="fp-art"><p class="fp-kicker">Del {e(x["range_label"])} de {e(x["year"])} · {x["articles"]} artículos</p>'
        f'<h3><a class="fp-a" href="{e(x["href"], quote=True)}">{e(x["headline"])}</a></h3>'
        + (f'<p>{e(x["thread"])}</p>' if x["thread"] else "")
        + f'<a class="fp-more" href="{e(x["href"], quote=True)}">Leer la edición →</a></article>' for x in entries)
    body = rows or '<p class="fp-meta">Todavía no hay ediciones.</p>'
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lo que se compartió · {e(chat_name)}</title>
<style>html,body{{margin:0}}
{CSS}</style>
</head>
<body><div class="fp-root"><div class="fp-wrap">
<header>
<h1 class="fp-title">Lo que se compartió</h1>
<p class="fp-sub">{e(chat_name)}</p>
<nav class="fp-band"><span>Todas las ediciones</span></nav>
</header>
<section class="fp-cols fp-index">{body}</section>
<footer class="fp-foot">Resúmenes generados automáticamente a partir de los enlaces compartidos en la comunidad. Pueden contener
errores; consultá la fuente original. Hecho con Community Lens.</footer>
</div></div></body>
</html>
"""
