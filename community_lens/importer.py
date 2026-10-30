"""Importación en dos pasos (vista previa → confirmación) y reconciliación.

Reconciliación de exportaciones solapadas
-----------------------------------------
No se deduplica por "mismo texto + autor + fecha". Se alinean dos SECUENCIAS
(la ya guardada del chat y la del archivo nuevo) con difflib.SequenceMatcher
dentro de la ventana temporal que comparten:

- 'equal'   → mismo mensaje en la misma posición relativa: se agrega solo una
              fuente (message_sources). Dos mensajes idénticos consecutivos
              alinean con dos, no con uno: las repeticiones legítimas sobreviven.
- 'insert'  → mensajes solo en el archivo nuevo: se agregan.
- 'delete'  → mensajes solo en lo ya guardado: se conservan (nunca se borra).
- 'replace' → tramo que difiere en ambos lados: se agregan los nuevos y, si
              comparten instante con uno existente, ambos se marcan para
              revisión. No hay fusión destructiva.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import parser as P
from .config import settings
from .resources import link_key

MAX_BYTES = 50 * 1024 * 1024


class ImportError_(Exception):
    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ImportError_("bad_timezone", f"Zona horaria no reconocida: {name}") from None


def to_utc(naive: datetime, tz: ZoneInfo) -> datetime:
    # Horas inexistentes/ambiguas por cambio de horario usan la primera
    # ocurrencia (fold=0); se documenta como limitación.
    return naive.replace(tzinfo=tz).astimezone(UTC)


def fmt_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_local(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def decode(data: bytes) -> tuple[str, list[str]]:
    warnings = []
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        warnings.append("El archivo no es UTF-8 válido; algunos caracteres se reemplazaron por �.")
    return text, warnings


MAX_TXT_BYTES = 300_000_000


def unzip_chat(data: bytes, file_name: str) -> tuple[bytes, str]:
    """Si `data` es el .zip que exporta WhatsApp, devuelve el TXT de adentro; si no, lo deja igual.
    Solo se abre la entrada de texto: los stickers los lee aparte zip_stickers y el resto de los adjuntos se ignora."""
    if not data.startswith(b"PK"):
        return data, file_name
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            texts = [i for i in z.infolist() if i.filename.lower().endswith(".txt") and not i.is_dir()]
            if not texts:
                raise ImportError_("zip_without_txt", "El .zip no trae ningún archivo .txt de chat.")
            entry = next((i for i in texts if i.filename.rsplit("/", 1)[-1] == "_chat.txt"), max(texts, key=lambda i: i.file_size))
            if entry.file_size > MAX_TXT_BYTES:
                raise ImportError_("zip_too_large", "El TXT dentro del .zip es demasiado grande.")
            inner = entry.filename.rsplit("/", 1)[-1]
            # "_chat.txt" no dice nada: el nombre del grupo suele estar en el del .zip.
            return z.read(entry), (file_name.rsplit(".", 1)[0] + ".txt") if inner == "_chat.txt" else inner
    except zipfile.BadZipFile:
        raise ImportError_("bad_zip", "El archivo parece un .zip pero no se pudo abrir.") from None


MAX_STICKER_BYTES = 2 * 1024 * 1024
MAX_STICKERS_TOTAL = 500 * 1024 * 1024


def is_webp(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def zip_stickers(data: bytes) -> dict[str, bytes]:
    """{nombre de archivo: bytes} de los stickers de un .zip exportado "con archivos"; vacío si no es un .zip.
    Solo entran archivos con nombre de sticker que de verdad son .webp, cada uno de hasta 2 MB; las demás
    fotos, audios y documentos no se abren."""
    if not data.startswith(b"PK"):
        return {}
    import io
    import zipfile
    out: dict[str, bytes] = {}
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for i in z.infolist():
                name = i.filename.rsplit("/", 1)[-1]
                if i.is_dir() or not P.is_sticker_file(name) or i.file_size > MAX_STICKER_BYTES:
                    continue
                with z.open(i) as f:
                    blob = f.read(MAX_STICKER_BYTES + 1)  # el tamaño declarado en el .zip no se da por bueno
                total += len(blob)
                if total > MAX_STICKERS_TOTAL:
                    raise ImportError_("zip_too_large", "Los stickers dentro del .zip suman demasiado.")
                if len(blob) <= MAX_STICKER_BYTES and is_webp(blob):
                    out[name] = blob
    except zipfile.BadZipFile:
        raise ImportError_("bad_zip", "El archivo parece un .zip pero no se pudo abrir.") from None
    return out


def suggest_name(file_name: str) -> str:
    n = Path(file_name).stem
    n = re.sub(r"^(WhatsApp Chat (?:with|-)\s*|Chat de WhatsApp con\s*|_chat$)", "", n, flags=re.I).strip()
    return n or Path(file_name).stem or "Chat"


def _key(ts_utc: str, author_raw: str | None, text: str, minute: bool) -> str:
    """Clave de reconciliación: instante (al segundo o al minuto), autor y texto sin marcadores de adjunto."""
    t = ts_utc[:17] + "00Z" if minute else ts_utc
    author = P.clean_author(author_raw) if author_raw else ""
    return hashlib.sha1(f"{t}\x1f{author}\x1f{P.match_text(text)}".encode()).hexdigest()


def match_key(ts_utc: datetime, author_raw: str | None, text: str, minute: bool) -> str:
    return _key(fmt_utc(ts_utc), author_raw, text, minute)


# ---------------------------------------------------------------- staging --

def stage(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise ImportError_("too_large", "El archivo supera 50 MB.")
    if not data.strip():
        raise ImportError_("empty", "El archivo está vacío.")
    sha = hashlib.sha256(data).hexdigest()
    d = settings().staging_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sha}.txt").write_bytes(data)
    return sha


def staged_bytes(sha: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ImportError_("bad_token", "Token de importación inválido.")
    f = settings().staging_dir / f"{sha}.txt"
    if not f.exists():
        raise ImportError_("not_staged", "La vista previa expiró; volvé a seleccionar el archivo.")
    return f.read_bytes()


def stage_stickers(sha: str, stickers: dict[str, bytes]) -> None:
    """Guarda junto al TXT los stickers del mismo .zip, para que la confirmación los encuentre. Sin stickers no queda
    archivo: una vista previa del mismo TXT sin archivos no hereda los de una anterior."""
    import zipfile
    f = settings().staging_dir / f"{sha}.stickers.zip"
    if not stickers:
        f.unlink(missing_ok=True)
        return
    with zipfile.ZipFile(f, "w", zipfile.ZIP_STORED) as z:
        for name, blob in stickers.items():
            z.writestr(name, blob)


def staged_stickers(sha: str) -> dict[str, bytes]:
    import zipfile
    f = settings().staging_dir / f"{sha}.stickers.zip"
    if not f.exists():
        return {}
    with zipfile.ZipFile(f) as z:
        return {i.filename: z.read(i) for i in z.infolist()}


def unstage(sha: str) -> None:
    (settings().staging_dir / f"{sha}.txt").unlink(missing_ok=True)
    (settings().staging_dir / f"{sha}.stickers.zip").unlink(missing_ok=True)


def clear_staging(older_than_seconds: float = 0) -> None:
    """Borra copias temporales de vistas previas no confirmadas."""
    d = settings().staging_dir
    if d.exists():
        limit = time.time() - older_than_seconds
        for f in [*d.glob("*.txt"), *d.glob("*.stickers.zip")]:
            if f.stat().st_mtime <= limit:
                f.unlink(missing_ok=True)


# ---------------------------------------------------------------- preview --

def _existing(con, chat_id: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT id, seq, ts_utc, author_raw, text FROM messages WHERE chat_id=? ORDER BY seq", (chat_id,))]


def _chat_minute_precision(con, chat_id: int, new_has_seconds: bool) -> bool:
    row = con.execute("SELECT MIN(has_seconds) m FROM imports WHERE chat_id=?", (chat_id,)).fetchone()
    return (not new_has_seconds) or (row["m"] == 0)


def _keys_for(existing: list[dict], minute: bool) -> list[str]:
    """Claves de lo ya guardado, SIEMPRE recalculadas desde el instante, el autor y el texto.
    La columna messages.match_key no se usa para comparar: en una base creada antes de que la clave ignorara
    los marcadores de adjunto guarda la clave vieja, y así ambas bases reconcilian igual sin migrar nada."""
    return [_key(e["ts_utc"], e["author_raw"], e["text"], minute) for e in existing]


def _one_second_twins(old: list[dict], recs, utcs, js: range) -> dict[int, int]:
    """{índice del registro nuevo: id del ya guardado} para los que son el mismo registro con un segundo de diferencia.
    WhatsApp no escribe siempre el mismo segundo: entre una exportación sin archivos y una con archivos del mismo
    chat, algunos registros (sobre todo avisos del sistema) salen corridos exactamente un segundo. Solo se mira
    dentro de un tramo donde ambos lados difieren, así que un repetido legítimo que está en los dos archivos no entra."""
    by_content: dict[tuple[str, str], list[dict]] = {}
    for e in old:
        by_content.setdefault((P.clean_author(e["author_raw"]) if e["author_raw"] else "", P.match_text(e["text"])), []).append(e)
    twins: dict[int, int] = {}
    for j in js:
        r = recs[j]
        for e in by_content.get((P.clean_author(r.author_raw) if r.author_raw else "", P.match_text(r.text)), ()):
            seen = datetime.strptime(e["ts_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            if e["id"] not in twins.values() and abs((seen - utcs[j]).total_seconds()) <= 1:
                twins[j] = e["id"]
                break
    return twins


def _window(existing: list[dict], lo_utc: str, hi_utc: str) -> tuple[int, int]:
    """Índices [i0, i1) de la secuencia existente que caen en la ventana temporal nueva."""
    idx = [i for i, e in enumerate(existing) if lo_utc <= e["ts_utc"] <= hi_utc]
    return (idx[0], idx[-1] + 1) if idx else (0, 0)


def _overlap(con, chat_id: int, recs, tz, has_seconds: bool) -> dict:
    existing = _existing(con, chat_id)
    minute = _chat_minute_precision(con, chat_id, has_seconds)
    if not existing or not recs:
        return {"existing_in_window": 0, "new_in_window": 0, "matched": 0, "ratio": None}
    utcs = [to_utc(r.ts, tz) for r in recs]
    lo, hi = fmt_utc(min(utcs)), fmt_utc(max(utcs))
    i0, i1 = _window(existing, lo, hi)
    ex_keys = Counter(_keys_for(existing[i0:i1], minute))
    e_lo, e_hi = existing[0]["ts_utc"], existing[-1]["ts_utc"]
    matched = new_in = 0
    for r, u in zip(recs, utcs, strict=True):
        if e_lo <= fmt_utc(u) <= e_hi:
            new_in += 1
            k = match_key(u, r.author_raw, r.text, minute)
            if ex_keys[k] > 0:
                ex_keys[k] -= 1
                matched += 1
    return {"existing_in_window": i1 - i0, "new_in_window": new_in, "matched": matched,
            "ratio": round(matched / new_in, 4) if new_in else None}


def preview(con, data: bytes, file_name: str, tz_name: str | None, date_order: str | None,
            stickers: dict[str, bytes] | None = None) -> dict:
    """`stickers`: los que zip_stickers sacó del mismo .zip; quedan en staging hasta confirmar."""
    tz_name = tz_name or settings().default_tz
    tz = get_tz(tz_name)
    clear_staging(older_than_seconds=3600)
    sha = stage(data)
    stage_stickers(sha, stickers or {})
    text, dec_warn = decode(data)
    raws, _issues, _ = P.scan(text)
    if not raws:
        unstage(sha)
        raise ImportError_("no_records", "No se reconoció ningún registro con fecha. ¿Es una exportación TXT de WhatsApp?")
    det = P.detect_date_order(raws)
    order = date_order or det["order"]
    out = {
        "token": sha, "file_name": file_name, "file_bytes": len(data),
        "suggested_name": suggest_name(file_name), "timezone": tz_name,
        "date_detection": det, "date_order": order,
        "date_order_source": "user" if date_order else ("detected" if det["order"] else None),
        "needs_date_order": order is None, "diagnostics": None, "candidates": [], "recommendation": None,
        "stickers_in_file": 0,
    }
    if date_order and det["order"] and date_order != det["order"]:
        out["date_order_warning"] = (
            f"Elegiste {date_order}, pero la evidencia del archivo indica {det['order']}. "
            "Los registros que no encajen quedarán marcados como fecha inválida.")
    if order is None:
        return out
    parsed = P.parse(text, order)
    diag = P.diagnostics(parsed)
    diag["warnings"] = dec_warn + diag["warnings"]
    for k in ("period_start", "period_end"):
        diag[k] = fmt_local(diag[k]) if diag[k] else None
    out["diagnostics"] = diag

    recs = parsed["records"]
    out["stickers_in_file"] = sum(1 for r in recs if r.attachment in (stickers or {}))
    sugg = out["suggested_name"].casefold()
    best = None
    for c in con.execute("SELECT id, name FROM chats ORDER BY id"):
        ov = _overlap(con, c["id"], recs, tz, parsed["has_seconds"])
        same_file = con.execute("SELECT id FROM imports WHERE chat_id=? AND file_sha256=?",
                                (c["id"], sha)).fetchone()
        cand = {"chat_id": c["id"], "name": c["name"], "same_file_import_id": same_file["id"] if same_file else None,
                "name_matches": c["name"].casefold() == sugg, **ov}
        out["candidates"].append(cand)
        strong = same_file or (ov["ratio"] is not None and ov["ratio"] >= 0.5 and ov["matched"] >= min(3, ov["new_in_window"]))
        if strong and (best is None or (ov["matched"] or 0) > (best["matched"] or 0)):
            best = cand
    if best:
        out["recommendation"] = {
            "action": "duplicate" if best["same_file_import_id"] else "update", "chat_id": best["chat_id"],
            "reason": ("Este archivo exacto ya fue importado en ese chat." if best["same_file_import_id"] else
                       f"{best['matched']} de {best['new_in_window']} registros del tramo compartido coinciden en secuencia y contenido.")}
    else:
        named = [c for c in out["candidates"] if c["name_matches"]]
        out["recommendation"] = {
            "action": "new", "chat_id": None,
            "reason": ("Hay un chat con el mismo nombre, pero el contenido no coincide: confirmá si es el mismo grupo."
                       if named else "No hay coincidencia de contenido con chats existentes.")}
    return out


# ----------------------------------------------------------------- commit --

def _author_id(con, cache: dict, chat_id: int, raw: str | None) -> int | None:
    if raw is None:
        return None
    if raw not in cache:
        row = con.execute("SELECT id FROM authors WHERE chat_id=? AND raw_label=?", (chat_id, raw)).fetchone()
        if row:
            cache[raw] = row["id"]
        else:
            cache[raw] = con.execute(
                "INSERT INTO authors(chat_id, raw_label, display_label, kind) VALUES (?,?,?,?)",
                (chat_id, raw, P.clean_author(raw), P.author_kind(raw))).lastrowid
    return cache[raw]


def commit(con, token: str, file_name: str, *, chat_id: int | None, chat_name: str | None,
           tz_name: str, date_order: str, confirm_low_overlap: bool = False) -> dict:
    data = staged_bytes(token)
    tz = get_tz(tz_name)
    text, dec_warn = decode(data)
    raws, _, _ = P.scan(text)
    det = P.detect_date_order(raws)
    if date_order not in P.DATE_ORDERS:
        raise ImportError_("date_order_required", "Falta elegir el orden de fecha.")
    parsed = P.parse(text, date_order)
    recs = parsed["records"]
    diag = P.diagnostics(parsed)
    diag["warnings"] = dec_warn + diag["warnings"]
    for k in ("period_start", "period_end"):
        diag[k] = fmt_local(diag[k]) if diag[k] else None

    # ¿Este archivo exacto ya está? Con un grupo elegido se mira solo ese grupo (el mismo archivo puede estar
    # en otro); sin grupo, cualquiera cuenta.
    other = con.execute(
        "SELECT i.id, i.chat_id, c.name FROM imports i JOIN chats c ON c.id=i.chat_id "
        "WHERE i.file_sha256=? AND (? IS NULL OR i.chat_id=?) ORDER BY i.id", (token, chat_id, chat_id)).fetchone()
    stickers = staged_stickers(token)
    if other:
        # El mismo TXT ya está, pero puede venir ahora en el .zip "con archivos": sus stickers sí se agregan.
        added = 0
        if stickers:
            con.execute("BEGIN IMMEDIATE")
            try:
                added = _attach_stickers(con, other["id"], recs, stickers)
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        unstage(token)
        return {"status": "duplicate", "chat_id": other["chat_id"], "import_id": other["id"], "stickers_added": added,
                "message": f"Este archivo ya estaba importado en «{other['name']}». " +
                           (f"Se agregaron las imágenes de {added} stickers; los mensajes no cambiaron." if added else "No se modificó nada.")}

    try:
        con.execute("BEGIN IMMEDIATE")
        if chat_id is None:
            name = (chat_name or suggest_name(file_name)).strip()[:200] or "Chat"
            chat_id = con.execute("INSERT INTO chats(name, timezone, created_at) VALUES (?,?,?)",
                                  (name, tz_name, now_iso())).lastrowid
            chat_tz = tz
            existing: list[dict] = []
        else:
            c = con.execute("SELECT timezone FROM chats WHERE id=?", (chat_id,)).fetchone()
            if not c:
                raise ImportError_("chat_not_found", "El chat seleccionado no existe.")
            chat_tz = get_tz(c["timezone"])
            existing = _existing(con, chat_id)
            ov = _overlap(con, chat_id, recs, tz, parsed["has_seconds"])
            if existing and ov["new_in_window"] >= 10 and (ov["ratio"] or 0) < 0.05 and not confirm_low_overlap:
                raise ImportError_(
                    "low_overlap",
                    f"El archivo comparte período con este chat pero solo {ov['matched']} de {ov['new_in_window']} "
                    "registros coinciden. Parece otro chat (o exportado con otra zona horaria o con autores "
                    "mostrados de otra forma). Confirmá explícitamente para agregarlo aquí.", overlap=ov)

        minute = _chat_minute_precision(con, chat_id, parsed["has_seconds"]) if existing else not parsed["has_seconds"]
        utcs = [to_utc(r.ts, tz) for r in recs]
        new_keys = [match_key(u, r.author_raw, r.text, minute) for r, u in zip(recs, utcs, strict=True)]
        stored_keys = [match_key(u, r.author_raw, r.text, False) for r, u in zip(recs, utcs, strict=True)]

        import_id = con.execute(
            """INSERT INTO imports(chat_id, file_name, file_sha256, file_bytes, imported_at, date_order,
                 date_order_source, clock, has_seconds, timezone, format_variant, diagnostics, reconciliation)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'{}')""",
            (chat_id, file_name[:300], token, len(data), now_iso(), date_order,
             "detected" if det["order"] == date_order else "user", parsed["clock"], int(parsed["has_seconds"]),
             tz_name, parsed["variant"], json.dumps(diag, ensure_ascii=False))).lastrowid
        for i in parsed["issues"]:
            con.execute("INSERT INTO import_issues(import_id,line_start,line_end,kind,detail,excerpt) VALUES (?,?,?,?,?,?)",
                        (import_id, i.line_start, i.line_end, i.kind, i.detail, i.excerpt))

        # --- alineación de secuencias dentro de la ventana compartida ---
        i0 = i1 = len(existing)
        if existing and recs:
            lo = fmt_utc(min(utcs))
            i0, i1 = _window(existing, lo, fmt_utc(max(utcs)))
            if i0 == i1:  # sin solape: se inserta en su lugar cronológico
                i0 = i1 = sum(1 for e in existing if e["ts_utc"] < lo)
        win = existing[i0:i1]
        win_keys = _keys_for(win, minute)
        ops = SequenceMatcher(None, win_keys, new_keys, autojunk=False).get_opcodes() if win else \
            [("insert", 0, 0, 0, len(recs))]

        uid_seen = Counter(_keys_for(existing, False))
        authors: dict = {}
        merged: list[int] = []  # ids en el orden final de la ventana
        summary = {"matched_existing": 0, "added": 0, "only_in_previous": 0, "ambiguous_pairs": 0,
                   "window_existing": len(win), "precision": "minuto" if minute else "segundo"}

        def add_source(mid: int, r) -> None:
            con.execute("INSERT OR IGNORE INTO message_sources(message_id,import_id,line_start,line_end,raw_ts) VALUES (?,?,?,?,?)",
                        (mid, import_id, r.line_start, r.line_end, r.raw_ts))

        def insert(j: int, review: str | None = None) -> int:
            r, u = recs[j], utcs[j]
            key = stored_keys[j]
            occ = uid_seen[key]
            uid_seen[key] += 1
            uid = hashlib.sha1(f"{chat_id}|{key}|{occ}|{import_id}".encode()).hexdigest()[:12]
            needs, reason = (True, review) if review else (r.needs_review, r.review_reason)
            mid = con.execute(
                """INSERT INTO messages(uid, chat_id, seq, author_id, author_raw, ts_written, ts_utc, ts_local,
                     record_type, subtype, text, content_text, media_kind, is_deleted, is_edited, is_poll,
                     link_count, needs_review, review_reason, match_key, first_import_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (uid, chat_id, -1, _author_id(con, authors, chat_id, r.author_raw if r.record_type == "message" else None),
                 r.author_raw, fmt_local(r.ts), fmt_utc(u), fmt_local(u.astimezone(chat_tz)),
                 r.record_type, r.subtype, r.text, r.content_text, r.media_kind, int(r.is_deleted),
                 int(r.is_edited), int(r.is_poll), len(r.links), int(bool(needs)), reason, key, import_id)).lastrowid
            con.execute("INSERT INTO messages_fts(rowid, content) VALUES (?,?)", (mid, r.content_text))
            for url, dom in r.links:
                con.execute("INSERT INTO links(message_id,url,domain) VALUES (?,?,?)", (mid, url, dom))
            add_source(mid, r)
            summary["added"] += 1
            return mid

        for tag, a0, a1, b0, b1 in ops:
            if tag == "equal":
                for k in range(a1 - a0):
                    add_source(win[a0 + k]["id"], recs[b0 + k])
                    merged.append(win[a0 + k]["id"])
                summary["matched_existing"] += a1 - a0
            elif tag == "delete":
                merged.extend(e["id"] for e in win[a0:a1])
                summary["only_in_previous"] += a1 - a0
            elif tag == "insert":
                merged.extend(insert(j) for j in range(b0, b1))
            else:  # replace: ambos lados difieren en este tramo
                twins = {} if minute else _one_second_twins(win[a0:a1], recs, utcs, range(b0, b1))
                summary["matched_existing"] += len(twins)
                summary["only_in_previous"] += a1 - a0 - len(twins)
                old = [e for e in win[a0:a1] if e["id"] not in twins.values()]
                flagged_old: set[int] = set()
                block: list[tuple[str, int, int | None]] = [(e["ts_utc"], 0, e["id"]) for e in win[a0:a1]]
                for j in range(b0, b1):
                    if j in twins:  # el mismo registro, un segundo corrido: se anota la fuente y no se inserta
                        add_source(twins[j], recs[j])
                        continue
                    u = fmt_utc(utcs[j].replace(second=0) if minute else utcs[j])
                    near = [e for e in old if (e["ts_utc"][:16] if minute else e["ts_utc"][:19]) == (u[:16] if minute else u[:19])]
                    review = "solape_ambiguo" if near else None
                    mid = insert(j, review)
                    if near:
                        summary["ambiguous_pairs"] += 1
                        flagged_old.update(e["id"] for e in near)
                    block.append((fmt_utc(utcs[j]), 1, mid))
                for eid in flagged_old:
                    con.execute("UPDATE messages SET needs_review=1, review_reason=COALESCE(review_reason,'solape_ambiguo') WHERE id=?", (eid,))
                block.sort(key=lambda t: (t[0], t[1]))
                merged.extend(b[2] for b in block)

        final = [e["id"] for e in existing[:i0]] + merged + [e["id"] for e in existing[i1:]]
        con.executemany("UPDATE messages SET seq=? WHERE id=?", [(n, mid) for n, mid in enumerate(final)])
        con.execute("UPDATE imports SET reconciliation=? WHERE id=?", (json.dumps(summary), import_id))
        stickers_added = _attach_stickers(con, import_id, recs, stickers)
        con.execute("COMMIT")
    except Exception:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    unstage(token)
    return {"status": "imported", "chat_id": chat_id, "import_id": import_id,
            "diagnostics": diag, "reconciliation": summary, "stickers_added": stickers_added}


def _attach_stickers(con, import_id: int, recs, stickers: dict[str, bytes]) -> int:
    """Asocia cada mensaje con el sticker que trae el .zip, buscándolo por la línea donde lo registró `import_id`.
    Vale igual para un mensaje recién insertado que para uno que ya estaba (por ejemplo, de una exportación sin
    archivos del mismo grupo). Devuelve cuántos mensajes quedaron con imagen por primera vez."""
    if not stickers:
        return 0
    by_line = {r["line_start"]: r["message_id"] for r in con.execute(
        "SELECT message_id, line_start FROM message_sources WHERE import_id=?", (import_id,))}
    added = 0
    for r in recs:
        mid = by_line.get(r.line_start)
        blob = stickers.get(r.attachment) if r.attachment else None
        if mid is None or blob is None:
            continue
        sha = hashlib.sha256(blob).hexdigest()
        con.execute("INSERT OR IGNORE INTO sticker_images(sha256, bytes, file_bytes) VALUES (?,?,?)", (sha, blob, len(blob)))
        added += con.execute("INSERT OR IGNORE INTO message_stickers(message_id, sha256) VALUES (?,?)", (mid, sha)).rowcount
        # Una importación anterior con archivos lo había guardado como adjunto genérico.
        con.execute("UPDATE messages SET media_kind='sticker' WHERE id=? AND media_kind='attachment'", (mid,))
    return added


# ------------------------------------------------------------ mantenimiento --

def retime_chat(con, chat_id: int, tz_name: str, reinterpret_imports: bool) -> None:
    """Cambia la zona del chat. Con reinterpret_imports también se redefine la
    zona declarada de sus archivos (corrige una elección equivocada al importar)."""
    chat_tz = get_tz(tz_name)
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("UPDATE chats SET timezone=? WHERE id=?", (tz_name, chat_id))
        if reinterpret_imports:
            con.execute("UPDATE imports SET timezone=? WHERE chat_id=?", (tz_name, chat_id))
        tzs = {r["id"]: get_tz(r["timezone"]) for r in con.execute("SELECT id, timezone FROM imports WHERE chat_id=?", (chat_id,))}
        rows = con.execute("SELECT id, ts_written, author_raw, text, first_import_id FROM messages WHERE chat_id=?", (chat_id,)).fetchall()
        upd = []
        for r in rows:
            u = to_utc(datetime.strptime(r["ts_written"], "%Y-%m-%d %H:%M:%S"), tzs[r["first_import_id"]])
            upd.append((fmt_utc(u), fmt_local(u.astimezone(chat_tz)), match_key(u, r["author_raw"], r["text"], False), r["id"]))
        con.executemany("UPDATE messages SET ts_utc=?, ts_local=?, match_key=? WHERE id=?", upd)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def delete_chat(con, chat_id: int) -> dict:
    """Elimina el chat y todo lo derivado (mensajes, índice, enlaces, clasificaciones, correcciones,
    ediciones del periódico, el texto de las páginas que se leyeron para ellas y las imágenes de stickers que
    ningún otro grupo usa)."""
    n = con.execute("SELECT COUNT(*) c FROM messages WHERE chat_id=?", (chat_id,)).fetchone()["c"]
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("DELETE FROM messages_fts WHERE rowid IN (SELECT id FROM messages WHERE chat_id=?)", (chat_id,))
        cur = con.execute("DELETE FROM chats WHERE id=?", (chat_id,))  # cascada al resto
        # pages no tiene chat_id (una página puede venir de varios grupos), así que la cascada no la alcanza:
        # se borra toda página cuyo enlace ya no figura en ningún grupo de los que quedan.
        kept = {link_key(r["url"]) for r in con.execute("SELECT DISTINCT url FROM links")}
        con.executemany("DELETE FROM pages WHERE url=?",
                        [(r["url"],) for r in con.execute("SELECT url FROM pages").fetchall() if link_key(r["url"]) not in kept])
        # Igual con los stickers: una imagen puede estar en varios grupos y solo se borra cuando ya no queda en ninguno.
        con.execute("DELETE FROM sticker_images WHERE sha256 NOT IN (SELECT sha256 FROM message_stickers)")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    if cur.rowcount:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")  # que el texto no quede recuperable en páginas libres
    return {"deleted": bool(cur.rowcount), "records_removed": n if cur.rowcount else 0}
