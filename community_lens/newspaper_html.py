"""Plantilla fija del periódico: un HTML autocontenido, sin scripts ni recursos externos.

Todo texto (del modelo o de las páginas) entra escapado; las direcciones salen de la base, no del modelo.
Recibe una edición con la forma actual (edition.Edition): las guardadas con una forma anterior ya pasaron
por edition.upgrade(), así que acá no hay valores por defecto. De edition.py toma solo los tipos (sin importarlo al
correr), porque edition.py dibuja con render() al aprobar una edición.
"""
from __future__ import annotations

from datetime import date
from html import escape as e
from pathlib import Path
from typing import TYPE_CHECKING

from .brand_icons import DRAWN, LOGOS
from .config import settings
from .dates import fmt_day, short_day

if TYPE_CHECKING:
    from .edition import Article, Edition, Reading

CSS = Path(__file__).with_name("newspaper.css").read_text(encoding="utf-8")
ICON = {"X": "𝕏", "YouTube": "▶️", "TikTok": "🎵", "Instagram": "📸", "LinkedIn": "💼", "Facebook": "📘", "Threads": "🧵",
        "Reddit": "👽", "Bluesky": "🦋", "Spotify": "🎧"}
READINGS_MARKER = b'<section class="fp-views" id="lecturas"'  # solo la plantilla lo produce: el texto entra escapado


def _title() -> str:
    """El título del periódico: "Lo que se compartió", precedido del nombre público si está configurado
    (CL_PERIODICO_NOMBRE). Ya escapado. Es lo único de la plantilla que sale de la configuración."""
    name = settings().newspaper_name
    return f"{e(name)} - Lo que se compartió" if name else "Lo que se compartió"


def _sub(fallback: str = "") -> str:
    """La línea bajo el título: el subtítulo público si está configurado (CL_PERIODICO_SUBTITULO), si no `fallback`
    (el nombre del grupo, solo en la versión completa). Ya con su etiqueta."""
    text = settings().newspaper_subtitle or fallback
    return f'\n<p class="fp-sub">{e(text)}</p>' if text else ""


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


def _mark(plat: str) -> str:
    """El logo de la red, chico, junto a su nombre: un SVG fijo de brand_icons.py, sin estilos en línea. Sin logo (una
    plataforma nueva), su ícono de texto."""
    layers = DRAWN.get(plat) or ((LOGOS[plat],) if plat in LOGOS else ())
    if layers:
        paths = "".join(f'<path fill="#{color}" d="{path}"/>' for color, path in layers)
        return f'<svg class="fp-mark" viewBox="0 0 24 24" aria-hidden="true">{paths}</svg>'
    return f'<span aria-hidden="true">{ICON.get(plat, "🔗")}</span>'


def _also(socials: list, tools: list) -> str:
    """Las publicaciones y los sitios de los que no se pudo leer nada: una línea al pie, con red o dominio, fecha y enlace.
    Una tarjeta vacía pesa lo mismo que una con contenido y se lee como rota."""
    entries = [(_mark(s["platform"]), s["platform"], s["url"], s["shared"]) for s in socials]
    entries += [('<span aria-hidden="true">🧰</span>', t["name"], t["url"], t["shared"]) for t in tools]
    links = [f'<a class="fp-also-a" href="{e(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
             f'{mark}<span>{e(name)} · {e(short_day(shared))}</span></a>' for mark, name, url, shared in entries]
    return '<p class="fp-also"><span>También se compartió:</span>' + "".join(links) + "</p>" if links else ""


OTHER_GROUPS = (("article", "Artículos"), ("social", "Redes sociales"), ("tool", "Sitios"))
OTHER_SHOW = 5        # por grupo; el resto queda plegado en "Ver N más"
OLD_DAYS = 30         # una nota publicada más de esto antes de compartirse lleva la marca "nota de …"
VIDEO = {"YouTube", "TikTok"}


def _old(published: str | None, shared: str) -> str:
    """La marca de nota vieja: solo si la página declara su fecha y es de más de OLD_DAYS antes de compartirse."""
    if not published or not shared:
        return ""
    p, s = date.fromisoformat(published), date.fromisoformat(shared[:10])
    if (s - p).days <= OLD_DAYS:
        return ""
    label = f"nota de {p.year}" if p.year != s.year else f"nota del {short_day(published)}"
    return f'<span class="fp-ol-old">{e(label)}</span>'


def _other(x: dict) -> str:
    """Un enlace de la lista: arriba el medio (o la red y la cuenta) y la fecha; abajo, el titular como enlace, en dos
    líneas a lo sumo. Sin titular, el dominio en gris: no se inventa nada."""
    if x["kind"] == "social":
        plat = x["platform"] or x["domain"]
        kick = _mark(plat) + f'<span class="fp-ol-src">{e(plat)}</span>'
        kick += f'<span class="fp-ol-acc">{e(x["account"])}</span>' if x["account"] else ""
        text = x["caption"] or (f"Video en {plat}" if plat in VIDEO else f"Publicación en {plat}")
        dim = not x["caption"]
    else:
        kick = f'<span class="fp-ol-src">{e(x["domain"])}</span>'
        text, dim = x["title"] or x["domain"], not x["title"]
    kick += f'<span class="fp-ol-d">{e(short_day(x["shared"]))}</span>' + _old(x["published"], x["shared"])
    return (f'<li class="fp-ol"><p class="fp-ol-k">{kick}</p><a class="fp-ol-t{" fp-ol-dim" if dim else ""}" '
            f'href="{e(x["url"], quote=True)}" target="_blank" rel="noopener noreferrer">{e(text)}</a></li>')


def _others(links: list) -> str:
    """Los enlaces que pasaron del tope: un índice por tipo, sin resúmenes, con los primeros OTHER_SHOW a la vista y el
    resto en un <details> (se abre sin JavaScript)."""
    groups = []
    for kind, label in OTHER_GROUPS:
        xs = [x for x in links if x["kind"] == kind]
        if not xs:
            continue
        head, tail = xs[:OTHER_SHOW], xs[OTHER_SHOW:]
        more = (f'<details class="fp-ol-more"><summary><span class="fp-ol-open">Ver {len(tail)} más</span>'
                f'<span class="fp-ol-close">Mostrar menos</span></summary><ul>{"".join(_other(x) for x in tail)}</ul></details>'
                if tail else "")
        groups.append(f'<div class="fp-ol-g"><h3><span>{label}</span><span class="fp-ol-n">{len(xs)}</span></h3>'
                      f'<ul>{"".join(_other(x) for x in head)}</ul>{more}</div>')
    if not groups:
        return ""
    return ('<h2 class="fp-section-h" id="otros">— Otros enlaces compartidos —</h2>'
            f'<section class="fp-others">{"".join(groups)}</section>')


def _card(tag: str, who: str, text: str, shared: str, url: str, cta: str, *, video=False, tool=False, anchor="") -> str:
    video_tag = '<span class="fp-tag">video</span>' if video else ""
    body = f"<p>{e(text)}</p>"
    who_line = f'<div class="fp-card-who">{e(who)}</div>' if who else ""
    return (f'<div class="fp-card{" fp-card-tool" if tool else ""}"{anchor}><div class="fp-item-h">{tag}{video_tag}</div>'
            f'{who_line}{body}'
            f'<div class="fp-card-foot">{_shared(shared)}{_link(url, cta, "fp-more")}</div></div>')


def _reading(r: Reading) -> str:
    topics = "".join(f"<li><span>{e(t['name'])}</span><b>{t['messages']}</b></li>" for t in r["topics"])
    topics_box = (f'<div class="fp-view-topics"><div class="fp-view-cap">Temas según {e(r["label"])} · mensajes contados por código</div>'
                  f"<ul>{topics}</ul></div>") if topics else ""
    return (f'<section class="fp-box fp-view"><h2>La visión de {e(r["label"])}</h2>'
            + "".join(f"<p>{e(p)}</p>" for p in r["paragraphs"]) + topics_box
            + f'<p class="fp-view-disc">Esta es la interpretación y el entendimiento que el modelo {e(r["model"])} hizo de los '
              "mensajes de este período.</p></section>")


def render(d: Edition, *, draft: bool = False, public: bool = False) -> str:
    """La página de una edición. Con draft, la banda "Borrador, no compartir" arriba y "BORRADOR · " en el título.
    Con public, la versión que se publica (la que se guarda al aprobar): sin lo que describe la conversación, es
    decir sin lecturas, sin la caja "En números" y sin el nombre del grupo; lo que viene de las páginas enlazadas
    queda. Sin public, la versión completa que se ve en la app. Se combinan libremente."""
    lead, arts, socials, tools, readings, f = d["lead"], d["articles"], d["socials"], d["tools"], d["readings"], d["facts"]
    plural = lambda n, one, many: f"{n} {one if n == 1 else many}"
    nav = [f'<a href="#articulos">📰 {plural(1 + len(arts), "artículo", "artículos")}</a>']
    if socials:
        nav.append(f'<a href="#redes">💬 {plural(len(socials), "publicación en redes", "publicaciones en redes")}</a>')
    if any(t["preview"] for t in tools):  # el ancla está en la primera tarjeta; los sin vista previa van en "También se compartió"
        n_tools = sum(t["preview"] for t in tools)
        nav.append(f'<a href="#sitios">🧰 {plural(n_tools, "sitio o herramienta", "sitios y herramientas")}</a>')
    # En la versión pública, lo que no se pudo leer va a la lista como un artículo más (con su dominio): el motivo
    # ("HTTP 404") es jerga para quien lee y queda solo en la versión completa.
    others = d["others"] + ([{"kind": "article", "url": u["url"], "shared": u["shared"], "domain": u["domain"], "platform": None,
                              "title": None, "published": None, "caption": None, "account": None} for u in d["unread"]] if public else [])
    if others:
        nav.append(f'<a href="#otros">🔗 {plural(len(others), "enlace más", "enlaces más")}</a>')
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
    facts_box = "" if public else f'<section class="fp-box"><h2>En números</h2><p class="fp-facts">{facts_line}</p></section>'
    side = f'<aside class="fp-side">{timeline}{facts_box}</aside>' if timeline or facts_box else ""
    lead_html = (f'<section class="fp-lead-grid{"" if side else " fp-solo"}" id="articulos"><article>'
                 + (f'<p class="fp-kicker">{e(lead["kicker"])}</p>' if lead["kicker"] else "")
                 + f'<h2 class="fp-lead-h">{_link(lead["url"], lead["headline"])}</h2>{_meta(lead, " · ")}'
                 + '<div class="fp-lead-body">' + "".join(f"<p>{e(s)}</p>" for s in lead["summary"]) + "</div>"
                 + (f'<p class="fp-note">{e(lead["note"])}</p>' if lead["note"] else "")
                 + _link(lead["url"], "Leer el artículo original →", "fp-more")
                 + f'</article>{side}</section>')

    views = ""
    if readings and not public:
        title = "Dos lecturas del período" if len(readings) == 2 else "Lecturas del período" if len(readings) > 2 else "Lectura del período"
        views = (f'<section class="fp-views" id="lecturas"><div class="fp-strip-h"><h2>{title}</h2></div>'
                 f'<div class="fp-views-row fp-n{min(len(readings), 3)}">' + "".join(_reading(r) for r in readings) + "</div></section>")

    strip = ""
    if socials or tools:
        cards = [_card(f'{_mark(s["platform"])}<span>{e(s["platform"])}</span>', s["account"] or "", s["text"], s["shared"], s["url"],
                       f'Ver en {s["platform"]} →', video=s["video"]) for s in socials if s["preview"]]
        cards += [_card('<span aria-hidden="true">🧰</span><span>Sitio</span>', t["name"], t["text"], t["shared"], t["url"], "Ir al sitio →",
                        tool=True, anchor=' id="sitios"' if i == 0 else "") for i, t in enumerate(x for x in tools if x["preview"])]
        strip = ('<section class="fp-strip" id="redes"><div class="fp-strip-h"><h2>Redes sociales y herramientas</h2>'
                 '<span class="fp-swipe">deslizá →</span></div>' + (f'<div class="fp-row">{"".join(cards)}</div>' if cards else "")
                 + _also([s for s in socials if not s["preview"]], [t for t in tools if not t["preview"]]) + "</section>")

    more = ('<h2 class="fp-section-h">— Más artículos compartidos —</h2><section class="fp-cols">'
            + "".join(_article(a) for a in arts) + "</section>") if arts else ""
    unread = ""
    if d["unread"] and not public:
        rows = "".join(f"<li>{_link(u['url'], u['domain'])} · compartido {e(short_day(u['shared']))} · {e(u['reason'])}</li>" for u in d["unread"])
        unread = f'<section class="fp-failed"><h2>No se pudo leer</h2><ul>{rows}</ul></section>'
    band = '\n<p class="fp-draft">Borrador, no compartir</p>' if draft else ""
    editor = d["editor"]
    foot = (f"Resúmenes generados automáticamente con {e(editor['label'])} ({e(editor['model'])}) a partir de los enlaces compartidos. "
            "Pueden contener errores; consultá la fuente original. "
            + ("Los enlaces y las fechas en que se compartieron salen del chat exportado. " if public else
               "Los enlaces y las cifras salen del chat exportado. ") +
            "Hecho con Community Lens.")
    group = "" if public else f" · {e(d['chat_name'])}"
    sub = _sub("" if public else d["chat_name"])

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{"BORRADOR · " if draft else ""}{_title()}{group} · {e(d['range_label'])}</title>
<style>html,body{{margin:0}}
{CSS}</style>
</head>
<body><div class="fp-root"><div class="fp-wrap">{band}
<header>
<div class="fp-top"><span>{e(d['created_label'])}</span><span>{"Enlaces" if public else "Datos"} del {e(d['range_label'])}</span></div>
<h1 class="fp-title">{_title()}</h1>{sub}
<nav class="fp-band" aria-label="Secciones">{''.join(nav)}</nav>
{thread}
</header>
{lead_html}
{views}
{strip}
{more}
{_others(others)}
{unread}
<footer class="fp-foot">{foot}</footer>
</div></div></body>
</html>
"""


def render_index(entries: list[dict]) -> str:
    """Portada del sitio: todas las ediciones, la más reciente primero. Solo la usa publish(), así que siempre es
    pública: no nombra al grupo."""
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
<title>{_title()}</title>
<style>html,body{{margin:0}}
{CSS}</style>
</head>
<body><div class="fp-root"><div class="fp-wrap">
<header>
<h1 class="fp-title">{_title()}</h1>{_sub()}
<nav class="fp-band"><span>Todas las ediciones</span></nav>
</header>
<section class="fp-cols fp-index">{body}</section>
<footer class="fp-foot">Resúmenes generados automáticamente a partir de los enlaces compartidos en la comunidad. Pueden contener
errores; consultá la fuente original. Hecho con Community Lens.</footer>
</div></div></body>
</html>
"""
