"""API HTTP local (FastAPI) y entrega del frontend estático."""
from __future__ import annotations

import ipaddress
import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import classify, edition, importer, llm, newspaper, report, resources, search, stats, stickers
from .classify import labels
from .classify.taxonomy import CATEGORY_KEYS, TAXONOMY_VERSION
from .classify.taxonomy import public as taxonomy_public
from .config import ROOT, settings
from .db import connect

log = logging.getLogger("community_lens")
WEB = ROOT / "web"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@asynccontextmanager
async def lifespan(_: FastAPI):
    importer.clear_staging()  # vistas previas abandonadas de sesiones anteriores
    con = connect()           # crea el esquema si hace falta
    classify.close_stale_runs(con)
    con.close()
    yield


# Observable Plot inserta dos bloques <style> fijos (gráfico y leyenda). Se autorizan por hash,
# sin abrir 'unsafe-inline'. Cambian solo si se actualiza web/vendor/plot.umd.min.js o className.
PLOT_STYLE_HASHES = ("'sha256-6MccQ6InvDkYV6vSLT2lawF4C9tQ+uF6qK4xSUxs29c=' "
                     "'sha256-ZaIsY78kKEERwDFq5FQUrBZv92AbP+thZJ0PXefAfZc='")

app = FastAPI(title="Community Lens", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def db():
    con = connect()
    try:
        yield con
    finally:
        con.close()


def _date(v: str | None) -> str | None:
    if v in (None, ""):
        return None
    if not _DATE.match(v):
        raise HTTPException(422, "Fecha inválida; usá AAAA-MM-DD.")
    return v


# ------------------------------------------------------ cuerpos de solicitud --
# La forma de cada cuerpo se valida al entrar (422 si no calza), antes de tocar la base. Es estricta: un
# número es un número y un booleano es un booleano; nada se convierte a mano en las rutas.
# Cada modelo tiene su tipo gemelo en web/src/types.ts; tests/test_contract.py comprueba que coincidan.

class _Body(BaseModel):
    model_config = ConfigDict(strict=True, populate_by_name=True)


class ChatUpdateRequest(_Body):
    name: str | None = None
    timezone: str | None = None
    reinterpret_imports: bool = True


class ImportCommitRequest(_Body):
    token: str = ""
    file_name: str | None = None
    chat_id: int | None = None
    chat_name: str | None = None
    timezone: str | None = None
    date_order: str = ""
    confirm_low_overlap: bool = False


class MergeAuthorRequest(_Body):
    into: int


class LabelsRequest(_Body):
    categories: list[str] | None = None
    note: str | None = None


class RunStartRequest(_Body):
    chat_id: int
    provider: str = "rules"
    date_from: str | None = Field(None, alias="from")
    date_to: str | None = Field(None, alias="to")
    consent: bool = False


class NewspaperRequest(_Body):
    chat_id: int
    date_from: str = Field(alias="from", min_length=1)  # generar envía texto afuera: el rango va siempre explícito
    date_to: str = Field(alias="to", min_length=1)
    editor: str = ""
    readers: list[str] = []
    consent_chat: bool = False
    order: Literal["recent", "shared"] = "recent"
    max_articles: int = 8


class ApproveRequest(_Body):
    by: str          # quién aprobó y por qué canal: obligatorios, los valida edition.approve (422 si no sirven)
    via: str
    to: str | None = None


def _chat(con, chat_id: int):
    c = con.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not c:
        raise HTTPException(404, "Chat no encontrado.")
    return c


# ------------------------------------------------ de dónde viene la solicitud --
# La app no tiene autenticación: confía en que solo le habla quien está en esta máquina. Pero el navegador de
# esa persona también corre páginas de otros, así que antes de atender nada se comprueban dos cosas:
#
# 1. Host: la solicitud tiene que estar dirigida a una dirección de esta instancia. Si no, una página ajena cuyo
#    nombre pasa a resolver a 127.0.0.1 ("DNS rebinding") sería, para el navegador, el mismo origen que la app
#    y podría leer toda la API. Vale para cualquier método y cualquier ruta, archivos estáticos incluidos.
# 2. Origin y Sec-Fetch-Site: una solicitud que modifica no puede declarar que viene de otro sitio. Aquí el Host
#    es el correcto (la página ajena le habla a la dirección real), y lo que la delata es lo que agrega el navegador.
#
# No se consulta X-Forwarded-Host ni ningún otro encabezado de proxy, ni request.url (que Starlette arma con ellos).

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")  # no modifican nada; todo otro método pasa por la comprobación 2
_HOSTNAME = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")  # sin comodines, sin puerto, sin punto final


def _is_ip(host: str) -> bool:
    """Una dirección IP escrita tal cual, como va en Host: IPv4, o IPv6 entre corchetes y sin zona."""
    try:
        if host.startswith("["):
            return host.endswith("]") and "%" not in host and ipaddress.ip_address(host[1:-1]).version == 6
        return ipaddress.ip_address(host).version == 4
    except ValueError:
        return False


def _own_host(value: str) -> str | None:
    """El host (en minúsculas, sin puerto) si `value` es un Host de esta instancia; None si no.
    La comparación es exacta: un nombre que contiene o termina en uno permitido no es uno permitido."""
    s = settings()
    value = value.lower()
    if not value.isascii():
        return None
    if value.startswith("["):  # IPv6: los dos puntos de adentro de los corchetes no separan el puerto
        end = value.find("]")
        host, rest = value[:end + 1], value[end + 1:]
        if end < 0 or (rest and not rest.startswith(":")):
            return None
        port = rest[1:] if rest else None
    else:
        host, sep, port = value.partition(":")
        port = port if sep else None
    # El puerto es siempre el configurado. Sin puerto, el navegador habla con el 80.
    if port != str(s.port) and not (port is None and s.port == 80):
        return None
    if host in ("localhost", "127.0.0.1", "[::1]"):
        return host
    # Además: CL_HOST si es un nombre o una IPv4, y los nombres que el dueño listó en CL_ALLOWED_HOSTS.
    if host in {n for n in (c.strip().lower() for c in (s.host, *s.allowed_hosts.split(","))) if _HOSTNAME.fullmatch(n)}:
        return host
    # Con CL_HOST fuera de loopback no se sabe por qué dirección va a entrar el dueño (con 0.0.0.0, cualquiera de
    # la máquina). Se acepta una IP escrita tal cual: cambiar el DNS solo sirve con un nombre, y un nombre no pasa.
    if not s.loopback and _is_ip(host):
        return host
    return None


def _refusal(request: Request) -> JSONResponse | None:
    """La respuesta de rechazo si la solicitud no está dirigida a esta instancia o viene de otro sitio."""
    hosts = request.headers.getlist("host")
    host = _own_host(hosts[0]) if len(hosts) == 1 else None  # ausente o repetido: no hay nada que comparar
    if host is None:
        return JSONResponse({"error": "host_not_allowed", "detail": "Esta dirección no es la de esta instancia de Community Lens. "
                             "Abrila con la dirección que muestra al arrancar; para usarla con otro nombre, agregalo a CL_ALLOWED_HOSTS en .env."},
                            status_code=421)
    if request.method in SAFE_METHODS:
        return None
    # El origen propio es el de la dirección por la que entró la solicitud. La app sirve http, nunca https.
    own = f"http://{host}" + ("" if settings().port == 80 else f":{settings().port}")
    origins, sites = request.headers.getlist("origin"), request.headers.getlist("sec-fetch-site")
    # Sin Origin ni Sec-Fetch-Site (curl, un script) no hay nada que indique otro sitio: el navegador los agrega
    # solo, y una página no puede quitarlos ni falsearlos. Si están, tienen que decir "la propia app".
    if (len(origins) > 1 or any(not (o.isascii() and o.lower() == own) for o in origins)
            or len(sites) > 1 or any(v.lower() not in ("same-origin", "none") for v in sites)):
        return JSONResponse({"error": "cross_origin", "detail": "La solicitud viene de otro sitio y no se ejecutó. "
                             "Los cambios solo se aceptan desde la propia app."}, status_code=403)
    return None


@app.middleware("http")
async def security_headers(request: Request, call_next):
    # Es el único middleware y envuelve todo (API, páginas y archivos montados): lo rechazado no llega a ninguna ruta.
    resp = _refusal(request) or await call_next(request)
    if not request.url.path.startswith(("/api/report", "/como-funciona", "/periodico/")):
        # Sin scripts ni estilos en línea, sin orígenes externos.
        resp.headers["Content-Security-Policy"] = (
            f"default-src 'self'; style-src 'self' {PLOT_STYLE_HASHES}; img-src 'self' data:; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.exception_handler(importer.ImportError_)
async def import_error(_, exc: importer.ImportError_):
    return JSONResponse({"error": exc.code, "detail": exc.message, **exc.extra}, status_code=409 if exc.code == "low_overlap" else 422)




# ------------------------------------------------------------------ config --

@app.get("/api/config")
def config():
    s = settings()
    return {"default_timezone": s.default_tz, "host": s.host, "port": s.port,
            "loopback": s.loopback,
            "taxonomy": {"version": TAXONOMY_VERSION, "categories": taxonomy_public()},
            "providers": classify.providers_info()}


# ------------------------------------------------------------------- chats --

@app.get("/api/chats")
def chats(con=Depends(db)):
    out = []
    for c in con.execute("SELECT * FROM chats ORDER BY id"):
        cov = stats.coverage(con, c["id"])
        n = con.execute("SELECT SUM(record_type='message') m, SUM(needs_review) r FROM messages WHERE chat_id=?", (c["id"],)).fetchone()
        out.append({**dict(c), **cov, "participant_messages": n["m"] or 0, "flagged_for_review": n["r"] or 0,
                    "imports": con.execute("SELECT COUNT(*) n FROM imports WHERE chat_id=?", (c["id"],)).fetchone()["n"]})
    return out


@app.patch("/api/chats/{chat_id}")
def update_chat(chat_id: int, body: ChatUpdateRequest, con=Depends(db)):
    _chat(con, chat_id)
    # Primero se valida todo; recién después se escribe. La zona va antes que el nombre porque es la
    # única de las dos escrituras que puede fallar (y lo hace dentro de su propia transacción).
    name = None
    if body.name is not None:
        name = body.name.strip()[:200]
        if not name:
            raise HTTPException(422, "El nombre no puede quedar vacío.")
    if body.timezone is not None:
        importer.get_tz(body.timezone)
        importer.retime_chat(con, chat_id, body.timezone, body.reinterpret_imports)
    if name is not None:
        con.execute("UPDATE chats SET name=? WHERE id=?", (name, chat_id))
    return dict(_chat(con, chat_id))


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: int, con=Depends(db)):
    _chat(con, chat_id)
    res = importer.delete_chat(con, chat_id)
    log.info("chat %s eliminado (%s registros)", chat_id, res["records_removed"])
    return res


@app.get("/api/chats/{chat_id}/imports")
def chat_imports(chat_id: int, con=Depends(db)):
    _chat(con, chat_id)
    out = []
    for r in con.execute("SELECT * FROM imports WHERE chat_id=? ORDER BY id", (chat_id,)):
        d = dict(r)
        d["diagnostics"] = json.loads(d["diagnostics"])
        d["reconciliation"] = json.loads(d["reconciliation"])
        d["issues"] = [dict(i) for i in con.execute(
            "SELECT line_start, line_end, kind, detail, excerpt FROM import_issues WHERE import_id=? ORDER BY line_start LIMIT 100", (r["id"],))]
        out.append(d)
    return out


# ----------------------------------------------------------------- imports --

@app.post("/api/imports/preview")
async def import_preview(request: Request, filename: str = Query(..., max_length=300), timezone: str | None = None,
                         date_order: str | None = Query(None, pattern="^(MDY|DMY|YMD)$"), con=Depends(db)):
    body = await request.body()
    data, name = importer.unzip_chat(body, Path(filename).name)
    res = importer.preview(con, data, name, timezone, date_order, stickers=importer.zip_stickers(body))
    log.info("vista previa: %s bytes, estado de fecha=%s", len(data), res["date_detection"]["status"])
    return res


@app.post("/api/imports/commit")
def import_commit(body: ImportCommitRequest, con=Depends(db)):
    res = importer.commit(
        con, body.token, Path(body.file_name or "chat.txt").name,
        chat_id=body.chat_id, chat_name=body.chat_name,
        tz_name=body.timezone or settings().default_tz, date_order=body.date_order,
        confirm_low_overlap=body.confirm_low_overlap)
    log.info("importación: estado=%s chat=%s", res["status"], res["chat_id"])
    return res


# ------------------------------------------------------------ estadísticas --

@app.get("/api/stats")
def get_stats(chat_id: int, date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"), con=Depends(db)):
    _chat(con, chat_id)
    return stats.compute(con, chat_id, _date(date_from), _date(date_to))


@app.get("/api/authors")
def get_authors(chat_id: int, date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"), con=Depends(db)):
    _chat(con, chat_id)
    return stats.authors(con, chat_id, _date(date_from), _date(date_to))


@app.post("/api/authors/{author_id}/merge")
def merge(author_id: int, body: MergeAuthorRequest, con=Depends(db)):
    try:
        stats.merge_author(con, author_id, body.into)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return {"ok": True}


@app.post("/api/authors/{author_id}/unmerge")
def unmerge(author_id: int, con=Depends(db)):
    stats.unmerge_author(con, author_id)
    return {"ok": True}


# ------------------------------------------------------ búsqueda / contexto --

@app.get("/api/search")
def do_search(q: str | None = Query(None, max_length=300), chat_id: int | None = None,
              date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
              author_id: int | None = None, category: str | None = None, record_type: str = "message",
              has_link: bool = False, has_media: bool = False, review_only: bool = False, human_only: bool = False,
              run_id: int | None = None, order: str = "recent", limit: int = 50, offset: int = 0, con=Depends(db)):
    if category and category not in CATEGORY_KEYS:
        raise HTTPException(422, "Categoría desconocida.")
    if record_type not in ("message", "system", "unrecognized", "all"):
        raise HTTPException(422, "Tipo de registro desconocido.")
    return search.search(con, q=q, chat_id=chat_id, d_from=_date(date_from), d_to=_date(date_to), author_id=author_id,
                         category=category or None, record_type=record_type, has_link=has_link, has_media=has_media,
                         review_only=review_only, human_only=human_only, run_id=run_id, order=order, limit=limit, offset=offset)


@app.get("/api/resources")
def get_resources(chat_id: int, kind: str = Query("document", pattern="^(document|link)$"), q: str | None = Query(None, max_length=200),
                  order: str = Query("recent", pattern="^(recent|shared)$"),
                  date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                  limit: int = 50, offset: int = 0, con=Depends(db)):
    """Documentos (agrupados por versión) o enlaces compartidos en el chat."""
    _chat(con, chat_id)
    return resources.library(con, chat_id, kind=kind, q=q, order=order, d_from=_date(date_from), d_to=_date(date_to),
                             limit=limit, offset=offset)


@app.get("/api/stickers")
def get_stickers(chat_id: int, date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                 limit: int = Query(60, ge=1, le=200), con=Depends(db)):
    """Ranking de los stickers con imagen del chat en el período, con quién los manda."""
    _chat(con, chat_id)
    return stickers.ranking(con, chat_id, _date(date_from), _date(date_to), limit)


@app.get("/api/stickers/{sha256}")
def get_sticker_image(sha256: str = PathParam(..., pattern="^[0-9a-f]{64}$"), con=Depends(db)):
    """La imagen tal como vino en el .zip. Ya se verificó que es .webp al importarla; nosniff la deja en imagen."""
    data = stickers.image(con, sha256)
    if data is None:
        raise HTTPException(404, "Ese sticker no está en la base.")
    return Response(data, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/messages/{message_id}/context")
def get_context(message_id: int, before: int = 6, after: int = 6, con=Depends(db)):
    try:
        return search.context(con, message_id, before, after)
    except KeyError:
        raise HTTPException(404, "Mensaje no encontrado.") from None


@app.post("/api/messages/{message_id}/labels")
def set_labels(message_id: int, body: LabelsRequest, con=Depends(db)):
    """Agrega una corrección humana. La predicción original no se toca.
    categories = null revierte a la clasificación automática."""
    try:
        cats = labels.save_correction(con, message_id, body.categories, body.note)
    except KeyError:
        raise HTTPException(404, "Mensaje no encontrado.") from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return {"ok": True, "categories": cats}


# ------------------------------------------------------------ clasificación --

@app.get("/api/classify/runs")
def runs(chat_id: int, con=Depends(db)):
    active = labels.active_run(con, chat_id)
    return [{**dict(r), "config": json.loads(r["config"]), "active": r["id"] == active} for r in
            con.execute("SELECT * FROM analysis_runs WHERE chat_id=? ORDER BY id DESC", (chat_id,))]


@app.post("/api/classify/runs/{run_id}/activate")
def activate_run(run_id: int, con=Depends(db)):
    """Elige qué ejecución se muestra como clasificación vigente del chat. No borra ninguna."""
    r = con.execute("SELECT chat_id FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Ejecución no encontrada.")
    try:
        labels.set_active_run(con, r["chat_id"], run_id)
    except KeyError:
        raise HTTPException(422, "Solo se puede activar una ejecución completada.") from None
    return {"ok": True, "active_run_id": run_id}


@app.post("/api/classify/runs")
def start_run(body: RunStartRequest, con=Depends(db)):
    _chat(con, body.chat_id)
    d_from, d_to = _date(body.date_from), _date(body.date_to)
    try:
        res = classify.run(con, body.chat_id, body.provider, d_from, d_to, consent=body.consent)
    except classify.ProviderError as e:
        raise HTTPException(400, str(e)) from None
    log.info("clasificación: proveedor=%s mensajes=%s errores=%s", res["provider"], res["classified"], res["errors"])
    return res


@app.get("/api/classify/summary")
def class_summary(chat_id: int, date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                  run_id: int | None = None, con=Depends(db)):
    _chat(con, chat_id)
    return search.category_summary(con, chat_id, _date(date_from), _date(date_to), run_id)


@app.get("/api/classify/monthly")
def class_monthly(chat_id: int, date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                  run_id: int | None = None, con=Depends(db)):
    _chat(con, chat_id)
    return search.category_monthly(con, chat_id, _date(date_from), _date(date_to), run_id)


# ------------------------------------------------------------------ reporte --

@app.get("/api/report")
def get_report(chat_id: int, format: str = Query("html", pattern="^(html|txt)$"),
               date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
               run_id: int | None = None, include_authors: bool = False, download: bool = False, con=Depends(db)):
    c = _chat(con, chat_id)
    r = report.build(con, chat_id, _date(date_from), _date(date_to), run_id, include_authors)
    body = report.to_html(r) if format == "html" else report.to_text(r)
    headers = {}
    if download:
        name = re.sub(r"[^\w\-]+", "_", c["name"], flags=re.U).strip("_") or "chat"
        headers["Content-Disposition"] = f"attachment; filename=\"reporte.{format}\"; filename*=UTF-8''{quote('reporte_' + name)}.{format}"
    if format == "html":
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return Response(body, media_type="text/html; charset=utf-8" if format == "html" else "text/plain; charset=utf-8", headers=headers)


# ---------------------------------------------------------------- periódico --

@app.exception_handler(newspaper.NewspaperError)
async def newspaper_error(_, exc: newspaper.NewspaperError):
    return JSONResponse({"error": "newspaper", "detail": str(exc)}, status_code=422)


@app.exception_handler(llm.LLMError)
async def llm_error(_, exc: llm.LLMError):
    return JSONResponse({"error": "llm", "detail": str(exc)}, status_code=502)


@app.get("/api/newspaper/preview")
def newspaper_preview(chat_id: int, date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                      order: str = Query("recent", pattern="^(recent|shared)$"), max_articles: int = 8, con=Depends(db)):
    """Qué entraría en la edición y qué se enviaría a un modelo. No sale nada de la máquina.
    Sin fechas, el rango por defecto. El rango y el máximo de artículos los valida newspaper (422 si no sirven)."""
    _chat(con, chat_id)
    return newspaper.preview(con, chat_id, date_from, date_to, order, max_articles)


@app.post("/api/newspaper/editions")
def newspaper_generate(body: NewspaperRequest, con=Depends(db)):
    c = _chat(con, body.chat_id)
    res = newspaper.generate(con, c["id"], c["name"], body.date_from, body.date_to, editor=body.editor, readers=body.readers,
                             consent_chat=body.consent_chat, order=body.order, max_articles=body.max_articles)
    log.info("periódico: edición %s, %s páginas a %s, %s mensajes a %s proveedor(es)", res["id"], res["sent"]["pages"],
             res["sent"]["pages_to"], res["sent"]["chat_messages"], len(res["sent"]["chat_to"]))
    return res


@app.get("/api/newspaper/editions")
def newspaper_editions(chat_id: int, con=Depends(db)):
    _chat(con, chat_id)
    return newspaper.editions(con, chat_id)


def _edition_change(con, edition_id: int, change) -> dict:
    """Aplica un cambio de estado y devuelve la edición con la forma de una fila de la lista."""
    if not con.execute("SELECT 1 FROM editions WHERE id=?", (edition_id,)).fetchone():
        raise HTTPException(404, "Edición no encontrada.")
    try:
        change()
    except ValueError as err:  # datos que no sirven o estado que no admite el cambio
        raise newspaper.NewspaperError(str(err)) from None
    return edition.listed(con, edition_id)


# Registradas en este orden: aprobar, descartar, recuperar (tests/test_origin.py las recorre así).
@app.post("/api/newspaper/editions/{edition_id}/approve")
def newspaper_approve(edition_id: int, body: ApproveRequest, con=Depends(db)):
    """Aprueba la edición: guarda los bytes que se publican, su hash y quién, por qué canal y adónde."""
    row = _edition_change(con, edition_id, lambda: edition.approve(con, edition_id, body.by, body.via, body.to))
    log.info("periódico: edición %s aprobada", edition_id)  # sin el nombre de quien aprueba
    return row


@app.post("/api/newspaper/editions/{edition_id}/discard")
def newspaper_discard(edition_id: int, con=Depends(db)):
    row = _edition_change(con, edition_id, lambda: edition.discard(con, edition_id))
    log.info("periódico: edición %s descartada", edition_id)
    return row


@app.post("/api/newspaper/editions/{edition_id}/restore")
def newspaper_restore(edition_id: int, con=Depends(db)):
    row = _edition_change(con, edition_id, lambda: edition.restore(con, edition_id))
    log.info("periódico: edición %s recuperada como borrador", edition_id)
    return row


@app.get("/periodico/{edition_id}")
def newspaper_page(edition_id: int, download: bool = False, borrador: bool = False, publico: bool = False, con=Depends(db)):
    """La edición como página autocontenida: sin scripts, sin red, solo estilos en línea. Lleva la banda de borrador
    si la edición es borrador o descartada, o si se pide con ?borrador=1 (para mandar a revisar una aprobada). Con
    ?publico=1, la versión que se publica, sin lecturas, cifras ni nombre del grupo; se combina con ?borrador=1."""
    found = newspaper.edition_html(con, edition_id, True if borrador else None, public=publico)
    if not found:
        raise HTTPException(404, "Edición no encontrada.")
    page, name = found
    headers = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{name}.html"'
    return Response(page, media_type="text/html; charset=utf-8", headers=headers)


# ----------------------------------------------------------------- frontend --

DIAGRAMS = {"": "flujo-de-datos.html", "periodico": "periodico.html"}


@app.get("/como-funciona")
@app.get("/como-funciona/{name}")
def how_it_works(name: str = ""):
    """Diagramas de flujo de datos generados con Archify (HTML autocontenido, sin datos de chats).
    Llevan scripts en línea, así que tienen su propia política: sin red y sin acceso a la API."""
    if name not in DIAGRAMS:
        raise HTTPException(404, "Ese diagrama no existe.")
    f = ROOT / "docs" / "diagramas" / DIAGRAMS[name]
    if not f.exists():
        raise HTTPException(404, "Diagrama no generado; ver docs/diagramas/README.md.")
    return FileResponse(f, media_type="text/html; charset=utf-8", headers={"Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
        "font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'")})


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
