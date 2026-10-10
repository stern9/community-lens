"""Parser determinista de exportaciones TXT de WhatsApp.

No depende de la base de datos ni de un LLM. Convierte texto en registros
tipados conservando la ubicación (líneas) de cada uno en el archivo original.

Principios:
- Cada línea del archivo termina en un registro o en un "issue" contabilizado.
- El orden día/mes nunca se asume: se busca evidencia y, si no alcanza, el
  llamador debe pedir una selección explícita.
- El contenido del chat es dato: nunca se evalúa ni se interpreta como instrucción.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from urllib.parse import urlsplit

# Marcas direccionales/invisibles que WhatsApp inserta. ‍ (ZWJ) se conserva
# porque forma parte de emojis compuestos.
_MARKS = "\u200e\u200f\ufeff\u202a\u202b\u202c\u2066\u2067\u2068\u2069\u200b"
_MARKS_RE = re.compile(f"[{_MARKS}]")
_SP = r"[   ]"

_DATE = r"(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})"
_TIME = rf"(\d{{1,2}})[:.](\d{{2}})(?:[:.](\d{{2}}))?(?:{_SP}*([AaPp])\.?{_SP}?[Mm]\.?)?"
# iOS / exportadores con corchetes: "[1/31/26, 21:24:17] Autor: texto"
_BRACKET_RE = re.compile(rf"^\[{_DATE},?{_SP}+{_TIME}\]{_SP}?(.*)$", re.S)
# Android: "31/1/26, 21:24 - Autor: texto"
_DASH_RE = re.compile(rf"^{_DATE},?{_SP}+{_TIME}{_SP}-{_SP}(.*)$", re.S)

DATE_ORDERS = ("MDY", "DMY", "YMD")

_MEDIA_WORDS = (
    r"image|video|audio|sticker|GIF|document|Contact card|voice message|video note|"
    r"imagen|v[ií]deo|documento|Tarjeta de contacto|nota de voz|Media|Multimedia"
)
_OMIT = r"omitted|omitida|omitido"
# Solo cuenta como marcador si viene con evidencia estructural: precedido por
# U+200E, envuelto en <...>, o si es el cuerpo completo del mensaje.
_MEDIA_MARKED_RE = re.compile(
    rf"(?:\u200e\s*({_MEDIA_WORDS})\s+(?:{_OMIT}))|(?:<({_MEDIA_WORDS})\s+(?:{_OMIT})>)", re.I
)
_MEDIA_WHOLE_RE = re.compile(rf"^({_MEDIA_WORDS})\s+(?:{_OMIT})$", re.I)
_ATTACHED_RE = re.compile(r"<(?:attached|adjunto):\s*([^>]{1,200})>", re.I)
_DOC_META_RE = re.compile(r"\s*•\s*\u200e?\d+\s+(?:pages?|páginas?)\s*$", re.I)
_EDITED_RE = re.compile(r"\u200e?<(?:This message was edited|Se editó este mensaje\.?)>", re.I)
_DELETED_RE = re.compile(
    r"^(?:This message was deleted(?: by admin .*?)?\.?|You deleted this message\.?|"
    r"Se eliminó este mensaje\.?|Eliminaste este mensaje\.?|Este mensaje fue eliminado\.?)$",
    re.I,
)
_POLL_RE = re.compile(r"^(?:POLL|ENCUESTA):", re.I)
_PLACEHOLDER_RE = re.compile(r"^<unknown message>$", re.I)
_LOCATION_RE = re.compile(r"^(?:Location|Ubicación):\s", re.I)

_MEDIA_KIND = {
    "image": "image", "imagen": "image", "video": "video", "vídeo": "video", "audio": "audio",
    "sticker": "sticker", "gif": "gif", "document": "document", "documento": "document",
    "contact card": "contact", "tarjeta de contacto": "contact", "voice message": "audio",
    "nota de voz": "audio", "video note": "video", "media": "unknown", "multimedia": "unknown",
}

# (subtipo, patrón) — avisos del sistema en inglés y español.
_SYSTEM_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("e2e_notice", re.compile(r"end-to-end encrypted|cifrad[oa]s? de extremo a extremo", re.I)),
    ("security_code", re.compile(r"security code|código de seguridad", re.I)),
    ("join", re.compile(
        r"\bjoined (?:using|from|via)\b|\bjoined$|\bwas added\b|\badded\b|se unió|añadió a|agregó a|fue añadid|"
        r"requested to join|solicitó unirse", re.I)),
    ("leave", re.compile(r"\bleft$|\bremoved\b|\bwas removed\b|salió del grupo|\bsalió$|eliminó a|fue eliminad", re.I)),
    ("group_change", re.compile(
        r"created (?:this )?group|creó (?:el|este) grupo|changed (?:the|this) group|changed the subject|"
        r"changed this group|changed the group|cambió (?:el|la|los) |Welcome to the group|Te damos la bienvenida|"
        r"This group has over|pinned a message|fijó un mensaje|changed their phone number|cambió su número|"
        r"reset this group|turned (?:on|off)|disappearing messages|mensajes temporales|"
        r"now an admin|ahora es admin|started a (?:video )?call|inició una (?:video)?llamada", re.I)),
    ("generic_notice", re.compile(r"^\[System notification\]$", re.I)),
]
_GROUP_AUTHOR_HINT = re.compile(
    r"end-to-end encrypted|cifrad[oa]s? de extremo a extremo|Welcome to the group|Te damos la bienvenida", re.I)

_WS_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'\u200e]+", re.I)
_PHONE_RE = re.compile(r"^\+?[\d\s\-() ‑.]{7,}$")
_SELF_LABELS = {"you", "tú", "tu", "usted", "yo"}


def strip_marks(s: str) -> str:
    return _MARKS_RE.sub("", s)


def match_text(text: str) -> str:
    """Forma del texto que se usa para reconocer el mismo mensaje en dos exportaciones.
    El marcador de adjunto no cuenta: una exportación "sin archivos" trae «image omitted» donde la exportación
    "con archivos" trae «<attached: 0001-PHOTO.jpg>», y es el mismo mensaje. Solo sirve para comparar."""
    t = _ATTACHED_RE.sub(" ", _MEDIA_MARKED_RE.sub(" ", text))
    t = _WS_RE.sub(" ", strip_marks(t)).strip()
    return "" if _MEDIA_WHOLE_RE.match(t) else t


def clean_author(raw: str) -> str:
    """Etiqueta legible del autor. No fusiona identidades: solo quita marcas."""
    s = strip_marks(raw).replace(" ", " ").replace(" ", " ").strip()
    if s.startswith("~"):
        s = s[1:].strip()
    return s


def author_kind(raw: str) -> str:
    s = strip_marks(raw).replace(" ", " ").strip()
    if s.lower() in _SELF_LABELS:
        return "self"  # quien exportó el archivo; su identidad no consta en el TXT
    if _PHONE_RE.match(s):
        return "phone"
    if s.startswith("~"):
        return "profile_name"  # nombre de perfil de alguien no guardado como contacto
    return "contact_name"


def extract_links(text: str) -> list[tuple[str, str]]:
    """Devuelve [(url, dominio)]. Una URL repetida en el mismo mensaje cuenta cada vez."""
    out = []
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?)]}*_~>»”’")
        probe = url if url.lower().startswith("http") else "http://" + url
        try:
            host = (urlsplit(probe).hostname or "").lower()
        except ValueError:
            host = ""
        if host.startswith("www."):
            host = host[4:]
        if "." not in host:
            continue
        out.append((url, host))
    return out


@dataclass
class RawRecord:
    line_start: int  # 1-based, inclusivo
    line_end: int
    d: tuple[int, int, int]
    d_width: tuple[int, int, int]
    hh: int
    mm: int
    ss: int | None
    ampm: str | None
    variant: str  # 'bracket' | 'dash'
    rest: str  # primera línea después del encabezado
    extra: list[str] = field(default_factory=list)
    raw_ts: str = ""


@dataclass
class ParsedRecord:
    line_start: int
    line_end: int
    ts: datetime  # hora local tal como aparece (sin zona)
    raw_ts: str
    record_type: str  # 'message' | 'system' | 'unrecognized'
    subtype: str
    author_raw: str | None
    text: str  # cuerpo original, sin modificar
    content_text: str  # texto sin marcadores, para búsqueda y clasificación
    media_kind: str | None = None
    is_deleted: bool = False
    is_edited: bool = False
    is_poll: bool = False
    needs_review: bool = False
    review_reason: str | None = None
    links: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Issue:
    line_start: int
    line_end: int
    kind: str  # 'preamble' | 'invalid_date'
    detail: str
    excerpt: str


def scan(text: str) -> tuple[list[RawRecord], list[Issue], int]:
    """Primera pasada: agrupa líneas en registros crudos sin interpretar fechas."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    raws: list[RawRecord] = []
    issues: list[Issue] = []
    cur: RawRecord | None = None
    pre_start = None
    pre_lines: list[str] = []
    for i, line in enumerate(lines, start=1):
        line = line.rstrip("\r")
        probe = line.lstrip(_MARKS)
        m = _BRACKET_RE.match(probe) if probe.startswith("[") else (
            _DASH_RE.match(probe) if probe[:1].isdigit() else None)
        if m:
            variant = "bracket" if probe.startswith("[") else "dash"
            a, b, c, hh, mm, ss, ap, rest = m.groups()
            cur = RawRecord(
                line_start=i, line_end=i, d=(int(a), int(b), int(c)),
                d_width=(len(a), len(b), len(c)), hh=int(hh), mm=int(mm),
                ss=int(ss) if ss is not None else None, ampm=ap.upper() if ap else None,
                variant=variant, rest=rest, raw_ts=probe[: m.start(8)].strip(),
            )
            raws.append(cur)
        elif cur is not None:
            cur.extra.append(line)
            cur.line_end = i
        else:
            if pre_start is None:
                pre_start = i
            pre_lines.append(line)
    if pre_lines and any(strip_marks(l).strip() for l in pre_lines):
        issues.append(Issue(pre_start, pre_start + len(pre_lines) - 1, "preamble",
                            "Líneas antes del primer registro con fecha", "\n".join(pre_lines)[:300]))
    return raws, issues, len(lines)


def detect_date_order(raws: list[RawRecord]) -> dict:
    """Busca evidencia del orden de fecha. No decide cuando la evidencia no alcanza."""
    if not raws:
        return {"status": "empty", "order": None, "evidence": {}}
    if any(r.d_width[0] == 4 for r in raws):
        return {"status": "detected", "order": "YMD",
                "evidence": {"reason": "El primer campo tiene 4 dígitos (año)."}}
    first_gt12 = [r for r in raws if r.d[0] > 12]
    second_gt12 = [r for r in raws if r.d[1] > 12]
    ev = {
        "first_field_gt12": len(first_gt12),
        "second_field_gt12": len(second_gt12),
        "example_line_first": first_gt12[0].line_start if first_gt12 else None,
        "example_line_second": second_gt12[0].line_start if second_gt12 else None,
        "example_raw": (first_gt12 or second_gt12 or raws)[0].raw_ts,
    }
    # Pista débil (no decisoria): las exportaciones son cronológicas.
    back = {}
    for order in ("MDY", "DMY"):
        n, prev = 0, None
        for r in raws:
            try:
                cur = _mk_date(r, order)
            except ValueError:
                n = None
                break
            if prev and cur < prev:
                n += 1
            prev = cur
        back[order] = n
    ev["backward_steps"] = back
    if first_gt12 and second_gt12:
        return {"status": "conflict", "order": None, "evidence": ev}
    if first_gt12:
        return {"status": "detected", "order": "DMY", "evidence": ev}
    if second_gt12:
        return {"status": "detected", "order": "MDY", "evidence": ev}
    hint = None
    if back["MDY"] == 0 and (back["DMY"] is None or back["DMY"] > 0):
        hint = "MDY"
    elif back["DMY"] == 0 and (back["MDY"] is None or back["MDY"] > 0):
        hint = "DMY"
    ev["hint"] = hint
    return {"status": "ambiguous", "order": None, "evidence": ev}


def _mk_date(r: RawRecord, order: str) -> tuple[int, int, int]:
    a, b, c = r.d
    if order == "MDY":
        y, mo, d = c, a, b
    elif order == "DMY":
        y, mo, d = c, b, a
    elif order == "YMD":
        y, mo, d = a, b, c
    else:
        raise ValueError(f"orden desconocido: {order}")
    if y < 100:
        y += 2000
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        raise ValueError("fecha fuera de rango")
    return (y, mo, d)


def _mk_ts(r: RawRecord, order: str) -> datetime:
    y, mo, d = _mk_date(r, order)
    hh = r.hh
    if r.ampm:
        if not 1 <= hh <= 12:
            raise ValueError("hora inválida para reloj de 12 h")
        hh = hh % 12 + (12 if r.ampm == "P" else 0)
    return datetime(y, mo, d, hh, r.mm, r.ss or 0)


def _split_author(rest: str) -> tuple[str | None, str]:
    idx = rest.find(": ")
    if idx == -1:
        if rest.endswith(":") and len(rest) <= 121:
            return rest[:-1], ""
        return None, rest
    if idx > 120:
        return None, rest
    return rest[:idx], rest[idx + 2:]


def _system_subtype(cleaned_first: str) -> str | None:
    for name, pat in _SYSTEM_PATTERNS:
        if pat.search(cleaned_first):
            return name
    return None


def parse(text: str, date_order: str) -> dict:
    """Interpreta el archivo completo con un orden de fecha explícito."""
    if date_order not in DATE_ORDERS:
        raise ValueError("date_order debe ser MDY, DMY o YMD")
    raws, issues, n_lines = scan(text)
    records: list[ParsedRecord] = []
    group_authors: set[str] = set()

    for r in raws:
        body_first = r.rest
        full_excerpt = (r.raw_ts + " " + r.rest)[:300]
        try:
            ts = _mk_ts(r, date_order)
        except ValueError as e:
            issues.append(Issue(r.line_start, r.line_end, "invalid_date",
                                f"No se pudo interpretar la fecha con orden {date_order}: {e}", full_excerpt))
            continue

        structural_system = False
        if r.variant == "bracket" and body_first.startswith("- "):
            # Exportadores de terceros marcan avisos como "[fecha] - texto"
            structural_system, author, body = True, None, body_first[2:]
        else:
            author, body = _split_author(body_first)
            if author is None:
                structural_system = True
        text_full = "\n".join([body] + r.extra)
        cleaned = strip_marks(text_full).strip()
        cleaned_first = strip_marks(body).strip()
        lrm_start = body.startswith("\u200e")

        rec = ParsedRecord(
            line_start=r.line_start, line_end=r.line_end, ts=ts, raw_ts=r.raw_ts,
            record_type="message", subtype="text", author_raw=author,
            text=text_full, content_text=cleaned,
        )

        if structural_system:
            rec.record_type = "system"
            rec.subtype = _system_subtype(cleaned_first) or "other"
            rec.content_text = cleaned
            records.append(rec)
            continue

        if author is not None and _GROUP_AUTHOR_HINT.search(cleaned_first) and lrm_start:
            group_authors.add(author)

        # --- marcadores de contenido de participante ---
        is_deleted = bool(_DELETED_RE.match(cleaned))
        edited = bool(_EDITED_RE.search(text_full))
        media_kind = None
        content = text_full
        mm = _MEDIA_MARKED_RE.search(text_full)
        if mm:
            media_kind = _MEDIA_KIND.get((mm.group(1) or mm.group(2)).lower(), "unknown")
            content = content[: mm.start()] + " " + content[mm.end():]
            if media_kind == "document":
                content = _DOC_META_RE.sub("", content)
        elif _MEDIA_WHOLE_RE.match(cleaned):
            media_kind = _MEDIA_KIND.get(_MEDIA_WHOLE_RE.match(cleaned).group(1).lower(), "unknown")
            content = ""
        am = _ATTACHED_RE.search(content)
        if am:
            media_kind = media_kind or "attachment"
            content = content[: am.start()] + " " + content[am.end():]
        content = _EDITED_RE.sub("", content)
        content = strip_marks(content).strip()

        sys_sub = _system_subtype(cleaned_first) if (lrm_start or author in group_authors) else None
        if author in group_authors or (sys_sub and lrm_start and not media_kind and not is_deleted):
            rec.record_type = "system"
            rec.subtype = sys_sub or "other"
            records.append(rec)
            continue

        rec.is_deleted = is_deleted
        rec.is_edited = edited
        rec.media_kind = media_kind
        rec.is_poll = bool(_POLL_RE.match(cleaned))
        if is_deleted:
            rec.subtype, content = "deleted", ""
        elif rec.is_poll:
            rec.subtype = "poll"
        elif _PLACEHOLDER_RE.match(cleaned):
            rec.subtype, content = "placeholder", ""
            rec.needs_review, rec.review_reason = True, "marcador_desconocido"
        elif media_kind and not content:
            rec.subtype = "media_only"
        elif _LOCATION_RE.match(cleaned):
            rec.subtype = "location"
        elif lrm_start and not media_kind:
            # Empieza con U+200E (típico de avisos) pero no coincide con ningún
            # patrón conocido: no se cuenta como participación ni como aviso.
            rec.record_type, rec.subtype = "unrecognized", "unknown_notice"
            rec.needs_review, rec.review_reason = True, "aviso_no_reconocido"
        rec.content_text = content
        if rec.record_type == "message":
            rec.links = extract_links(content)
        records.append(rec)

    # Un autor identificado como "el grupo" solo se conoce al terminar la pasada:
    # corregimos registros anteriores a su detección.
    if group_authors:
        for rec in records:
            if rec.author_raw in group_authors and rec.record_type != "system":
                rec.record_type, rec.subtype = "system", _system_subtype(strip_marks(rec.text)) or "other"
                rec.needs_review, rec.review_reason, rec.links = False, None, []

    return {
        "records": records,
        "issues": issues,
        "n_lines": n_lines,
        "variant": raws[0].variant if raws else None,
        "clock": ("12h" if any(r.ampm for r in raws) else "24h") if raws else None,
        "has_seconds": any(r.ss is not None for r in raws),
        "group_labels": sorted(clean_author(a) for a in group_authors),
    }


def diagnostics(parsed: dict) -> dict:
    recs: list[ParsedRecord] = parsed["records"]
    issues: list[Issue] = parsed["issues"]
    msgs = [r for r in recs if r.record_type == "message"]
    media: dict[str, int] = {}
    for r in msgs:
        if r.media_kind:
            media[r.media_kind] = media.get(r.media_kind, 0) + 1
    sys_sub: dict[str, int] = {}
    for r in recs:
        if r.record_type == "system":
            sys_sub[r.subtype] = sys_sub.get(r.subtype, 0) + 1
    invalid = [i for i in issues if i.kind == "invalid_date"]
    pre = [i for i in issues if i.kind == "preamble"]
    n_media = sum(media.values())
    warnings = []
    if n_media:
        warnings.append(
            f"{n_media} mensajes hacen referencia a adjuntos que no están en el TXT "
            "(multimedia omitida). Su contenido no se puede buscar ni clasificar.")
    n_del = sum(1 for r in msgs if r.is_deleted)
    if n_del:
        warnings.append(f"{n_del} mensajes aparecen como eliminados; su texto original no está disponible.")
    if not parsed["has_seconds"] and recs:
        warnings.append("El archivo no incluye segundos; la reconciliación entre exportaciones es menos precisa.")
    return {
        "lines_total": parsed["n_lines"],
        "records_detected": len(recs) + len(invalid),
        "participant_messages": len(msgs),
        "system_notices": sum(sys_sub.values()),
        "system_by_subtype": sys_sub,
        "unrecognized": sum(1 for r in recs if r.record_type == "unrecognized"),
        "invalid_date_records": len(invalid),
        "preamble_blocks": len(pre),
        "flagged_for_review": sum(1 for r in recs if r.needs_review),
        "multiline_records": sum(1 for r in recs if r.line_end > r.line_start),
        "media_omitted_messages": n_media,
        "media_by_kind": media,
        "deleted_messages": n_del,
        "edited_messages": sum(1 for r in msgs if r.is_edited),
        "distinct_author_labels": len({r.author_raw for r in msgs}),
        "period_start": min((r.ts for r in recs), default=None),
        "period_end": max((r.ts for r in recs), default=None),
        "format_variant": parsed["variant"],
        "clock": parsed["clock"],
        "has_seconds": parsed["has_seconds"],
        "warnings": warnings,
        "issues": [asdict(i) for i in issues[:50]],
    }
