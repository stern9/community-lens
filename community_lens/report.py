"""Reporte agregado para compartir (HTML autocontenido y texto plano).

Contiene únicamente agregados: ni mensajes, ni citas. Los identificadores de
autor solo se incluyen si se pide explícitamente.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from . import search, stats
from .classify.taxonomy import CATEGORIES

WEEKDAYS = ["Domingo", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"]
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre"]


def month_label(ym: str) -> str:
    """'2026-03' → 'mar-26'."""
    return f"{MONTHS[int(ym[5:7]) - 1][:3]}-{ym[2:4]}"


PROVIDER_LABEL = {"rules": "reglas locales (heurística, sin IA)", "jev": "Jev de TypeSafe"}


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f} %".replace(".", ",")


def _d(x, digits: int = 1) -> str:
    """Decimal con coma y separador de miles fino."""
    return "—" if x is None else f"{x:,.{digits}f}".replace(",", "\u202f").replace(".", ",")


def _n(x) -> str:
    return f"{x:,}".replace(",", " ")


def build(con, chat_id: int, d_from=None, d_to=None, run_id=None, include_authors=False) -> dict:
    s = stats.compute(con, chat_id, d_from, d_to)
    cats = search.category_summary(con, chat_id, d_from, d_to, run_id)
    au = stats.authors(con, chat_id, d_from, d_to)["authors"][:10] if include_authors else None
    return {"stats": s, "categories": cats, "authors": au,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}


def _metric_rows(s: dict) -> list[tuple[str, str, str]]:
    t, c, a = s["totals"], s["concentration"], s["averages"]
    m = t["participant_messages"]
    share = lambda n: f"{_pct(n / m if m else None)} de {_n(m)} mensajes de participantes"
    return [
        ("Mensajes de participantes", _n(m), f"de {_n(t['records'])} registros; {_n(t['system_notices'])} son avisos del sistema y no cuentan"),
        ("Identificadores de autor activos", _n(t["active_authors"]), "nombres o teléfonos tal como aparecen; no equivale a personas ni a miembros"),
        ("Días activos", _n(t["active_days"]), f"de {_n(t['conversation_days'])} días desde el primer mensaje; {_n(t['silent_days'])} sin mensajes"),
        ("Promedio de mensajes por día", _d(a["per_day"]), f"{_n(m)} mensajes en {_n(a['per_day_denominator'])} días de conversación"),
        ("Promedio de mensajes por mes", _d(a["per_month"]),
         f"{_n(a['per_month_messages'])} mensajes en {a['per_month_denominator']} meses completos; no cuenta los parciales"
         if a["per_month_denominator"] else "no hay meses completos en el período"),
        ("Promedio de mensajes por hora", _d(a["per_hour"], 2),
         f"contando las 24 horas; la hora pico es {a['peak_hour']:02d}:00, con {_d(a['peak_hour_per_day'])} mensajes por día en esa hora"
         if a["peak_hour"] is not None else "sin mensajes en el período"),
        ("Mensajes con enlaces", _n(t["messages_with_links"]), share(t["messages_with_links"])),
        ("Enlaces en total", _n(t["total_links"]), f"hacia {_n(t['distinct_domains'])} dominios distintos"),
        ("Mensajes con multimedia omitida", _n(t["media_omitted_messages"]), share(t["media_omitted_messages"])),
        ("Mensajes eliminados", _n(t["deleted_messages"]), share(t["deleted_messages"])),
        (f"Los {c['top5']['n']} participantes más activos", _pct(c["top5"]["share"]), f"escribieron {_n(c['top5']['messages'])} de {_n(m)} mensajes; mide volumen, no calidad"),
        (f"Los {c['top10']['n']} participantes más activos", _pct(c["top10"]["share"]), f"escribieron {_n(c['top10']['messages'])} de {_n(m)} mensajes; mide volumen, no calidad"),
    ]


def _period_line(s: dict) -> str:
    p = s["period"]
    if not p["first_day"]:
        return "Sin registros en el período."
    sel = "todo el historial importado" if not (p["from"] or p["to"]) else f"filtro {p['from'] or 'inicio'} → {p['to'] or 'fin'}"
    return f"{p['first_day']} a {p['last_day']} ({sel}) · zona horaria {s['chat']['timezone']}"


def _cat_block(cats: dict) -> tuple[str, list[tuple[str, int]]]:
    run = cats["run"]
    if not run:
        return "No se ejecutó ninguna clasificación para este chat.", []
    head = (f"Clasificación automática con {PROVIDER_LABEL.get(run['provider'], run['provider'])} "
            f"(modelo/versión {run['model']}, taxonomía {run['taxonomy_version']}, ejecución #{run['id']} del {run['started_at'][:10]}). "
            f"{_n(cats['classified_messages'])} de {_n(cats['participant_messages'])} mensajes de participantes clasificados; "
            f"{_n(cats['corrected_messages'])} corregidos a mano. Son estimaciones automáticas, no hechos verificados.")
    return head, [(CATEGORIES[k][0], v) for k, v in cats["effective"].items()]


def t_conv(s: dict) -> str:
    return _n(s["totals"]["conversation_days"])


def _silent_html(s: dict) -> str:
    sd = s["conversation"]["silent_days"]
    if not sd:
        return ""
    shown = ", ".join(f"{WEEKDAYS[d['weekday']][:3].lower()} {escape(d['day'])}" for d in sd[:40])
    return (f'<p class="n"><b>Días sin mensajes ({len(sd)} de {t_conv(s)} días de conversación):</b> '
            f'{shown}{" …" if len(sd) > 40 else ""}</p>')


def to_text(r: dict) -> str:
    s = r["stats"]
    L = [f"REPORTE AGREGADO — {s['chat']['name']}", _period_line(s), f"Generado: {r['generated_at']} con Community Lens (local)", "",
         "HECHOS CALCULADOS (conteos deterministas sobre el archivo importado)"]
    for label, val, note in _metric_rows(s):
        L.append(f"- {label}: {val} ({note})")
    sd = s["conversation"]["silent_days"]
    if sd:
        shown = ", ".join(f"{WEEKDAYS[d['weekday']][:3].lower()} {d['day']}" for d in sd[:40])
        L += ["", f"DÍAS SIN MENSAJES ({len(sd)} de {t_conv(s)} días de conversación)", shown + (" …" if len(sd) > 40 else "")]
    L += ["", "ACTIVIDAD MENSUAL (mensajes de participantes · identificadores activos)"]
    for m in s["monthly"]:
        flag = f"  [mes parcial: {m['days_covered']} de {m['days_in_month']} días]" if m["partial"] else ""
        L.append(f"- {month_label(m['month'])}: {_n(m['messages'])} · {_n(m['authors'])}{flag}")
    L += ["", "POR DÍA DE LA SEMANA"] + [f"- {WEEKDAYS[i]}: {_n(n)}" for i, n in enumerate(s["weekday"])]
    L += ["", "DOMINIOS MÁS COMPARTIDOS (mensajes que lo mencionan · enlaces)"]
    L += [f"- {d['domain']}: {_n(d['messages'])} · {_n(d['links'])}" for d in s["domains"][:15]] or ["- (ninguno)"]
    head, rows = _cat_block(r["categories"])
    L += ["", "CLASIFICACIONES AUTOMÁTICAS (no son conteos oficiales)", head]
    L += [f"- {k}: {_n(v)}" for k, v in rows]
    if rows:
        L.append("Un mensaje puede tener varias categorías; los conteos no suman el total.")
    if r["authors"]:
        L += ["", "IDENTIFICADORES CON MÁS MENSAJES (volumen, no calidad)"]
        L += [f"- {a['label']}: {_n(a['messages'])} ({_pct(a['share'])})" for a in r["authors"]]
    L += ["", "LIMITACIONES",
          "- El contenido no incluye adjuntos, reacciones ni la lista de miembros; no se calculan 'miembros silenciosos'.",
          "- Un identificador de autor puede no corresponder a una persona única.",
          "- Los meses marcados como parciales no son comparables con meses completos.",
          "- Las cifras describen actividad visible en el grupo; no dicen nada sobre conversaciones privadas, "
          "intenciones ni seguimiento fuera del chat.",
          "- Este reporte no contiene mensajes ni citas del historial."]
    return "\n".join(L) + "\n"


def _bars(monthly: list[dict]) -> str:
    if not monthly:
        return "<p>Sin datos.</p>"
    mx = max(m["messages"] for m in monthly) or 1
    W, H, pad = 860, 220, 8
    bw = (W - pad) / len(monthly)
    out = [f'<svg viewBox="0 0 {W} {H + 40}" role="img" aria-label="Mensajes de participantes por mes" style="width:100%;height:auto">',
           '<defs><pattern id="h" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
           '<rect width="6" height="6" fill="#9ec5f4"/><rect width="3" height="6" fill="#2a78d6"/></pattern></defs>',
           f'<line x1="{pad}" y1="{H}" x2="{W}" y2="{H}" stroke="#c9c8c2"/>']
    for i, m in enumerate(monthly):
        h = max(2, m["messages"] / mx * (H - 24))
        x = pad + i * bw + 2
        fill = "url(#h)" if m["partial"] else "#2a78d6"
        out.append(f'<rect x="{x:.1f}" y="{H - h:.1f}" width="{max(bw - 4, 2):.1f}" height="{h:.1f}" rx="3" fill="{fill}">'
                   f'<title>{escape(month_label(m["month"]))}: {_n(m["messages"])} mensajes{" (mes parcial)" if m["partial"] else ""}</title></rect>')
        out.append(f'<text x="{x + (bw - 4) / 2:.1f}" y="{H - h - 5:.1f}" text-anchor="middle" font-size="11" fill="#52514e">{_n(m["messages"])}</text>')
        out.append(f'<text x="{x + (bw - 4) / 2:.1f}" y="{H + 16}" text-anchor="middle" font-size="11" fill="#52514e">{escape(month_label(m["month"]))}{"*" if m["partial"] else ""}</text>')
    out.append("</svg>")
    return "".join(out)


def to_html(r: dict) -> str:
    s = r["stats"]
    e = escape
    cards = "".join(f'<div class="c"><div class="l">{e(l)}</div><div class="v">{e(v)}</div><div class="n">{e(n)}</div></div>'
                    for l, v, n in _metric_rows(s))
    months = "".join(
        f"<tr><td>{e(month_label(m['month']))}{' *' if m['partial'] else ''}</td><td>{_n(m['messages'])}</td><td>{_n(m['authors'])}</td>"
        f"<td>{m['days_covered']} de {m['days_in_month']}</td></tr>" for m in s["monthly"])
    wd = "".join(f"<tr><td>{WEEKDAYS[i]}</td><td>{_n(n)}</td></tr>" for i, n in enumerate(s["weekday"]))
    doms = "".join(f"<tr><td>{e(d['domain'])}</td><td>{_n(d['messages'])}</td><td>{_n(d['links'])}</td></tr>" for d in s["domains"][:15])
    head, rows = _cat_block(r["categories"])
    cats = "".join(f"<tr><td>{e(k)}</td><td>{_n(v)}</td></tr>" for k, v in rows)
    authors = ""
    if r["authors"]:
        authors = ("<h2>Identificadores con más mensajes</h2><p class='n'>Volumen, no calidad. Un identificador puede no ser una persona única.</p>"
                   "<table><tr><th>Identificador</th><th>Mensajes</th><th>% del total</th></tr>" +
                   "".join(f"<tr><td>{e(a['label'])}</td><td>{_n(a['messages'])}</td><td>{_pct(a['share'])}</td></tr>" for a in r["authors"]) + "</table>")
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Reporte agregado — {e(s['chat']['name'])}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;color:#0b0b0b;background:#fcfcfb;max-width:860px;margin:0 auto;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:16px;margin:28px 0 8px}}.n{{color:#52514e;font-size:13px}}
.g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px;margin-top:14px}}
.c{{border:1px solid #e3e2dc;border-radius:8px;padding:12px;background:#fff}}.l{{font-size:13px;color:#52514e}}.v{{font-size:24px;font-weight:600}}
table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{text-align:left;padding:5px 8px;border-bottom:1px solid #e3e2dc}}
td:not(:first-child),th:not(:first-child){{text-align:right;font-variant-numeric:tabular-nums}}
.tag{{display:inline-block;font-size:12px;border:1px solid #c9c8c2;border-radius:99px;padding:1px 8px;color:#52514e}}</style></head><body>
<h1>{e(s['chat']['name'])}</h1><div class="n">Reporte agregado · {e(_period_line(s))}<br>Generado {e(r['generated_at'])} con Community Lens (local). No contiene mensajes ni citas.</div>
<h2>Hechos calculados <span class="tag">conteos deterministas</span></h2><div class="g">{cards}</div>
{_silent_html(s)}
<h2>Mensajes de participantes por mes</h2>{_bars(s['monthly'])}
<p class="n">* Mes parcial (barra rayada): el historial o el período no lo cubren completo; no es comparable con meses completos.</p>
<table><tr><th>Mes</th><th>Mensajes</th><th>Identificadores activos</th><th>Días cubiertos</th></tr>{months}</table>
<h2>Por día de la semana</h2><table><tr><th>Día</th><th>Mensajes</th></tr>{wd}</table>
<h2>Dominios más compartidos</h2><table><tr><th>Dominio</th><th>Mensajes que lo mencionan</th><th>Enlaces</th></tr>{doms}</table>
<h2>Clasificaciones automáticas <span class="tag">estimación, no conteo oficial</span></h2><p class="n">{e(head)}</p>
{'<table><tr><th>Categoría</th><th>Mensajes</th></tr>' + cats + '</table><p class="n">Un mensaje puede tener varias categorías; los conteos no suman el total.</p>' if rows else ''}
{authors}
<h2>Limitaciones</h2><ul class="n">
<li>El contenido no incluye adjuntos, reacciones ni la lista de miembros; no se calculan «miembros silenciosos».</li>
<li>Un identificador de autor puede no corresponder a una persona única.</li>
<li>La cantidad de mensajes mide volumen, no la calidad del aporte.</li>
<li>Las cifras describen actividad visible en el grupo; no dicen nada sobre conversaciones privadas, intenciones ni seguimiento fuera del chat.</li>
</ul></body></html>"""
