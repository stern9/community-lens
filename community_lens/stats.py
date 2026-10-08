"""Estadísticas oficiales: solo SQL determinista. Ningún LLM interviene aquí."""
from __future__ import annotations

import calendar
from datetime import date, timedelta

# Identidad usada para contar autores: el alias reconciliado si existe.
AUTHOR_KEY = "COALESCE(a.canonical_id, a.id)"

DEFINITIONS = {
    "participant_messages": "Registros escritos por un identificador de autor, incluidos multimedia omitida, "
                            "encuestas y mensajes eliminados. Excluye avisos del sistema y registros no reconocidos.",
    "system_notices": "Avisos generados por WhatsApp (entradas, salidas, códigos de seguridad, cambios del grupo). "
                      "No cuentan como participación.",
    "active_authors": "Identificadores de autor distintos con al menos un mensaje de participante en el período. "
                      "Un identificador es un nombre o teléfono tal como aparece en el archivo: una misma persona "
                      "puede figurar con varios, y no equivale a 'miembros del grupo'.",
    "active_days": "Días calendario (zona del chat) con al menos un mensaje de participante.",
    "averages": "Promedio por día = mensajes de participantes ÷ días de conversación (incluye los días sin mensajes). "
                "Promedio por mes = solo meses completos; los parciales se excluyen. Promedio por hora = mensajes ÷ "
                "(días de conversación × 24), contando madrugadas. 'Hora pico' es la hora del día con más mensajes "
                "acumulados y su promedio por día en esa hora.",
    "conversation_days": "Días calendario desde el primer hasta el último mensaje de participante del chat, recortados "
                         "por el período elegido. No incluye los días previos en que el grupo existía pero solo "
                         "hay avisos del sistema. Es el denominador de 'días activos'.",
    "silent_days": "Días de conversación sin ningún mensaje de participante (puede haber avisos del sistema). "
                   "Días activos + días sin mensajes = días de conversación.",
    "calendar_days": "Días calendario entre el primer y el último registro del período, ambos incluidos.",
    "concentration": "Porcentaje de los mensajes de participantes del período escritos por los N identificadores "
                     "con más mensajes. Mide volumen, no calidad ni valor del aporte.",
    "messages_with_links": "Mensajes de participantes que contienen al menos una URL.",
    "total_links": "Cantidad total de URLs encontradas; un mensaje con tres URLs suma tres.",
    "domains": "Dominio (sin 'www.') de cada URL. 'mensajes' cuenta mensajes distintos que lo mencionan; "
               "'enlaces' cuenta apariciones.",
    "media_omitted": "Mensajes de participantes con un marcador de adjunto no incluido en el TXT "
                     "(imagen, video, audio, sticker, GIF, documento, contacto).",
    "deleted": "Mensajes que el archivo muestra como eliminados. Cuentan como mensaje de participante; su texto no existe.",
    "partial_month": "Mes que el historial o el período elegido no cubren del primer al último día. "
                     "No es comparable con meses completos.",
    "weekday_hour": "Mensajes de participantes por día de la semana y hora local (zona del chat).",
    "concentration_curve": "Para cada N, porcentaje acumulado de los mensajes de participantes escritos por los N "
                           "identificadores con más mensajes. 'half_rank' es el N más chico que llega al 50 %. "
                           "Describe reparto de volumen, no importancia ni calidad.",
    "silent_members": "No se calcula. El TXT no contiene la lista de miembros; restar 'miembros actuales' menos "
                      "'autores históricos' mezclaría momentos distintos y daría un número sin sentido.",
}


def _where(chat_id: int, d_from: str | None, d_to: str | None, alias: str = "m") -> tuple[str, list]:
    w, p = [f"{alias}.chat_id = ?"], [chat_id]
    if d_from:
        w.append(f"substr({alias}.ts_local,1,10) >= ?")
        p.append(d_from)
    if d_to:
        w.append(f"substr({alias}.ts_local,1,10) <= ?")
        p.append(d_to)
    return " AND ".join(w), p


def coverage(con, chat_id: int) -> dict:
    r = con.execute("SELECT MIN(ts_local) a, MAX(ts_local) b, COUNT(*) n FROM messages WHERE chat_id=?", (chat_id,)).fetchone()
    return {"first_record": r["a"], "last_record": r["b"], "records": r["n"]}


def _month_bounds(ym: str) -> tuple[str, str]:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{ym}-01", f"{ym}-{calendar.monthrange(y, m)[1]:02d}"


def compute(con, chat_id: int, d_from: str | None = None, d_to: str | None = None) -> dict:
    chat = con.execute("SELECT id, name, timezone FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not chat:
        raise KeyError("chat")
    cov = coverage(con, chat_id)
    w, p = _where(chat_id, d_from, d_to)
    one = lambda sql, params=p: con.execute(sql, params).fetchone()

    by_type = {r["record_type"]: r["n"] for r in con.execute(
        f"SELECT record_type, COUNT(*) n FROM messages m WHERE {w} GROUP BY record_type", p)}
    msgs = by_type.get("message", 0)
    wm = w + " AND m.record_type = 'message'"

    t = one(f"""SELECT COUNT(DISTINCT substr(m.ts_local,1,10)) active_days,
                       MIN(m.ts_local) first_ts, MAX(m.ts_local) last_ts,
                       SUM(m.media_kind IS NOT NULL) media, SUM(m.is_deleted) deleted, SUM(m.is_edited) edited,
                       SUM(m.is_poll) polls, SUM(m.link_count > 0) with_links, SUM(m.link_count) links,
                       SUM(m.content_text <> '') with_text
                FROM messages m WHERE {wm}""")
    span = one(f"SELECT MIN(substr(m.ts_local,1,10)) a, MAX(substr(m.ts_local,1,10)) b FROM messages m WHERE {w}")
    cal_days = (date.fromisoformat(span["b"]) - date.fromisoformat(span["a"])).days + 1 if span["a"] else 0

    # Días de conversación: desde el primer hasta el último mensaje de PARTICIPANTE del chat
    # (recortado por el filtro). Los días previos, con solo avisos del sistema, no cuentan.
    pm = con.execute("SELECT MIN(substr(ts_local,1,10)) a, MAX(substr(ts_local,1,10)) b FROM messages "
                     "WHERE chat_id=? AND record_type='message'", (chat_id,)).fetchone()
    conv_days, silent, conv_lo, conv_hi = 0, [], None, None
    if pm["a"]:
        conv_lo, conv_hi = max(pm["a"], d_from or pm["a"]), min(pm["b"], d_to or pm["b"])
        if conv_lo <= conv_hi:
            active = {r["d"] for r in con.execute(
                f"SELECT DISTINCT substr(m.ts_local,1,10) d FROM messages m WHERE {wm}", p)}
            d, end = date.fromisoformat(conv_lo), date.fromisoformat(conv_hi)
            conv_days = (end - d).days + 1
            while d <= end:
                if d.isoformat() not in active:
                    silent.append({"day": d.isoformat(), "weekday": (d.weekday() + 1) % 7})  # 0 = domingo
                d += timedelta(days=1)
        else:
            conv_lo = conv_hi = None

    authors = con.execute(
        f"""SELECT {AUTHOR_KEY} k, COUNT(*) n FROM messages m JOIN authors a ON a.id = m.author_id
            WHERE {wm} GROUP BY k ORDER BY n DESC, k""", p).fetchall()
    counts = [r["n"] for r in authors]
    attributed = sum(counts)

    def top(n: int) -> dict:
        s = sum(counts[:n])
        return {"n": min(n, len(counts)), "messages": s, "denominator": msgs,
                "share": round(s / msgs, 4) if msgs else None}

    # Curva acumulada: qué parte de los mensajes escriben los N identificadores más activos.
    curve, acc = [], 0
    for rank, c in enumerate(counts, start=1):
        acc += c
        curve.append({"rank": rank, "messages": acc, "share": round(acc / msgs, 4) if msgs else None})
    half_rank = next((p["rank"] for p in curve if msgs and p["messages"] * 2 >= msgs), None)

    daily = [dict(r) for r in con.execute(
        f"""SELECT substr(m.ts_local,1,10) day, COUNT(*) messages, COUNT(DISTINCT {AUTHOR_KEY}) authors
            FROM messages m LEFT JOIN authors a ON a.id = m.author_id WHERE {wm} GROUP BY day ORDER BY day""", p)]

    monthly = []
    cov_a, cov_b = (cov["first_record"] or "")[:10], (cov["last_record"] or "")[:10]
    lo = max(x for x in (cov_a, d_from or "") if x) if cov_a else None
    hi = min(x for x in (cov_b, d_to or "9999") if x) if cov_b else None
    for r in con.execute(
        f"""SELECT substr(m.ts_local,1,7) month, COUNT(*) messages, COUNT(DISTINCT {AUTHOR_KEY}) authors,
                   COUNT(DISTINCT substr(m.ts_local,1,10)) active_days
            FROM messages m LEFT JOIN authors a ON a.id = m.author_id WHERE {wm} GROUP BY month ORDER BY month""", p):
        m0, m1 = _month_bounds(r["month"])
        c0, c1 = max(m0, lo), min(m1, hi)
        partial = c0 > m0 or c1 < m1
        monthly.append({**dict(r), "partial": partial, "covered_from": c0, "covered_to": c1,
                        "days_in_month": int(m1[8:]),
                        "days_covered": (date.fromisoformat(c1) - date.fromisoformat(c0)).days + 1})

    wh = [dict(r) for r in con.execute(
        f"""SELECT CAST(strftime('%w', m.ts_local) AS INTEGER) weekday,
                   CAST(strftime('%H', m.ts_local) AS INTEGER) hour, COUNT(*) messages
            FROM messages m WHERE {wm} GROUP BY weekday, hour""", p)]
    weekday = [0] * 7
    hour = [0] * 24
    for r in wh:
        weekday[r["weekday"]] += r["messages"]
        hour[r["hour"]] += r["messages"]

    # Promedios. Cada uno declara su denominador; los meses parciales no entran en el promedio mensual.
    full = [x for x in monthly if not x["partial"]]
    peak_hour = max(range(24), key=lambda i: hour[i]) if msgs else None
    averages = {
        "per_day": round(msgs / conv_days, 1) if conv_days else None,
        "per_day_denominator": conv_days,
        "per_active_day": round(msgs / (t["active_days"] or 0), 1) if t["active_days"] else None,
        "per_month": round(sum(x["messages"] for x in full) / len(full), 1) if full else None,
        "per_month_denominator": len(full),
        "per_month_messages": sum(x["messages"] for x in full),
        "per_hour": round(msgs / (conv_days * 24), 2) if conv_days else None,
        "per_hour_denominator": conv_days * 24,
        "peak_hour": peak_hour,
        "peak_hour_messages": hour[peak_hour] if peak_hour is not None else 0,
        "peak_hour_per_day": round(hour[peak_hour] / conv_days, 1) if peak_hour is not None and conv_days else None,
    }

    domains = [dict(r) for r in con.execute(
        f"""SELECT l.domain, COUNT(DISTINCT l.message_id) messages, COUNT(*) links
            FROM links l JOIN messages m ON m.id = l.message_id WHERE {wm}
            GROUP BY l.domain ORDER BY messages DESC, links DESC, l.domain LIMIT 40""", p)]
    n_domains = one(f"SELECT COUNT(DISTINCT l.domain) n FROM links l JOIN messages m ON m.id=l.message_id WHERE {wm}")["n"]
    media = {r["media_kind"]: r["n"] for r in con.execute(
        f"SELECT m.media_kind, COUNT(*) n FROM messages m WHERE {wm} AND m.media_kind IS NOT NULL GROUP BY 1", p)}
    system = {r["subtype"]: r["n"] for r in con.execute(
        f"SELECT m.subtype, COUNT(*) n FROM messages m WHERE {w} AND m.record_type='system' GROUP BY 1", p)}
    review = one(f"SELECT COUNT(*) n FROM messages m WHERE {w} AND m.needs_review=1")["n"]
    n_alias = one(f"SELECT COUNT(DISTINCT m.author_id) n FROM messages m WHERE {wm}")["n"]

    return {
        "chat": dict(chat),
        "period": {"from": d_from, "to": d_to, "first_message": t["first_ts"], "last_message": t["last_ts"],
                   "first_day": span["a"], "last_day": span["b"], "calendar_days": cal_days},
        "coverage": {**cov, "note": "El último día suele estar incompleto: termina en el momento de la exportación."},
        "totals": {
            "records": sum(by_type.values()),
            "participant_messages": msgs,
            "system_notices": by_type.get("system", 0),
            "unrecognized": by_type.get("unrecognized", 0),
            "flagged_for_review": review,
            "messages_with_text": t["with_text"] or 0,
            "active_authors": len(counts),
            "author_labels": n_alias,
            "attributed_messages": attributed,
            "active_days": t["active_days"] or 0,
            "calendar_days": cal_days,
            "conversation_days": conv_days,
            "silent_days": len(silent),
            "messages_with_links": t["with_links"] or 0,
            "total_links": t["links"] or 0,
            "distinct_domains": n_domains,
            "media_omitted_messages": t["media"] or 0,
            "deleted_messages": t["deleted"] or 0,
            "edited_messages": t["edited"] or 0,
            "polls": t["polls"] or 0,
        },
        "averages": averages,
        "conversation": {"first_day": conv_lo, "last_day": conv_hi, "days": conv_days, "silent_days": silent},
        "concentration": {"top5": top(5), "top10": top(10), "curve": curve, "half_rank": half_rank,
                          "denominator": msgs},
        "daily": daily, "monthly": monthly,
        "weekday": weekday, "hour": hour, "weekday_hour": wh,
        "domains": domains, "media_by_kind": media, "system_by_subtype": system,
        "definitions": DEFINITIONS,
    }


def authors(con, chat_id: int, d_from: str | None = None, d_to: str | None = None) -> dict:
    w, p = _where(chat_id, d_from, d_to)
    total = con.execute(f"SELECT COUNT(*) n FROM messages m WHERE {w} AND m.record_type='message'", p).fetchone()["n"]
    rows = con.execute(
        f"""SELECT {AUTHOR_KEY} k, COUNT(*) messages, COUNT(DISTINCT substr(m.ts_local,1,10)) active_days,
                   MIN(substr(m.ts_local,1,10)) first_day, MAX(substr(m.ts_local,1,10)) last_day
            FROM messages m JOIN authors a ON a.id = m.author_id
            WHERE {w} AND m.record_type='message' GROUP BY k ORDER BY messages DESC, k""", p).fetchall()
    meta = {r["id"]: dict(r) for r in con.execute("SELECT id, display_label, raw_label, kind, canonical_id FROM authors WHERE chat_id=?", (chat_id,))}
    aliases: dict[int, list] = {}
    for a in meta.values():
        if a["canonical_id"]:
            aliases.setdefault(a["canonical_id"], []).append({"id": a["id"], "label": a["display_label"], "kind": a["kind"]})
    out = []
    for r in rows:
        a = meta[r["k"]]
        out.append({"id": a["id"], "label": a["display_label"], "kind": a["kind"], "messages": r["messages"],
                    "share": round(r["messages"] / total, 4) if total else None, "active_days": r["active_days"],
                    "first_day": r["first_day"], "last_day": r["last_day"], "aliases": aliases.get(a["id"], [])})
    return {"denominator": total, "authors": out,
            "note": "Cada fila es un identificador tal como aparece en el archivo (nombre de contacto, nombre de "
                    "perfil o teléfono), no necesariamente una persona única. La cantidad de mensajes mide volumen, "
                    "no la calidad del aporte."}


def merge_author(con, author_id: int, into_id: int) -> None:
    a = con.execute("SELECT id, chat_id FROM authors WHERE id=?", (author_id,)).fetchone()
    b = con.execute("SELECT id, chat_id, canonical_id FROM authors WHERE id=?", (into_id,)).fetchone()
    if not a or not b or a["chat_id"] != b["chat_id"] or author_id == into_id:
        raise ValueError("Los alias deben ser distintos y del mismo chat.")
    target = b["canonical_id"] or b["id"]
    if target == author_id:
        raise ValueError("Ese alias ya depende del que querés unir.")
    con.execute("UPDATE authors SET canonical_id=? WHERE id=? OR canonical_id=?", (target, author_id, author_id))


def unmerge_author(con, author_id: int) -> None:
    con.execute("UPDATE authors SET canonical_id=NULL WHERE id=?", (author_id,))
