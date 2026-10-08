"""API HTTP local (FastAPI) y entrega del frontend estático."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import classify, importer, report, resources, search, stats
from .classify.taxonomy import CATEGORY_KEYS, TAXONOMY_VERSION, public as taxonomy_public
from .config import ROOT, settings
from .db import connect

log = logging.getLogger("community_lens")
WEB = ROOT / "web"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")



@asynccontextmanager
async def lifespan(_: FastAPI):
    importer.clear_staging()  # vistas previas abandonadas de sesiones anteriores
    connect().close()         # crea el esquema si hace falta
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


def _chat(con, chat_id: int):
    c = con.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not c:
        raise HTTPException(404, "Chat no encontrado.")
    return c


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    if not request.url.path.startswith(("/api/report", "/como-funciona")):
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
            "loopback": s.host in ("127.0.0.1", "localhost", "::1"),
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
def update_chat(chat_id: int, body: dict = Body(...), con=Depends(db)):
    _chat(con, chat_id)
    if "name" in body:
        name = str(body["name"]).strip()[:200]
        if not name:
            raise HTTPException(422, "El nombre no puede quedar vacío.")
        con.execute("UPDATE chats SET name=? WHERE id=?", (name, chat_id))
    if "timezone" in body:
        importer.retime_chat(con, chat_id, str(body["timezone"]), bool(body.get("reinterpret_imports", True)))
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
    data = await request.body()
    res = importer.preview(con, data, Path(filename).name, timezone, date_order)
    log.info("vista previa: %s bytes, estado de fecha=%s", len(data), res["date_detection"]["status"])
    return res


@app.post("/api/imports/commit")
def import_commit(body: dict = Body(...), con=Depends(db)):
    res = importer.commit(
        con, str(body.get("token", "")), Path(str(body.get("file_name") or "chat.txt")).name,
        chat_id=body.get("chat_id"), chat_name=body.get("chat_name"),
        tz_name=str(body.get("timezone") or settings().default_tz), date_order=str(body.get("date_order", "")),
        confirm_low_overlap=bool(body.get("confirm_low_overlap")))
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
def merge(author_id: int, body: dict = Body(...), con=Depends(db)):
    try:
        stats.merge_author(con, author_id, int(body.get("into", 0)))
    except ValueError as e:
        raise HTTPException(422, str(e))
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


@app.get("/api/messages/{message_id}/context")
def get_context(message_id: int, before: int = 6, after: int = 6, con=Depends(db)):
    try:
        return search.context(con, message_id, before, after)
    except KeyError:
        raise HTTPException(404, "Mensaje no encontrado.")


@app.post("/api/messages/{message_id}/labels")
def set_labels(message_id: int, body: dict = Body(...), con=Depends(db)):
    """Agrega una corrección humana. La predicción original no se toca.
    categories = null revierte a la clasificación automática."""
    if not con.execute("SELECT 1 FROM messages WHERE id=?", (message_id,)).fetchone():
        raise HTTPException(404, "Mensaje no encontrado.")
    cats = body.get("categories")
    if cats is not None:
        if not isinstance(cats, list) or any(c not in CATEGORY_KEYS for c in cats):
            raise HTTPException(422, "Categorías inválidas.")
        cats = [c for c in CATEGORY_KEYS if c in cats]
    con.execute("INSERT INTO human_labels(message_id, categories, note, created_at) VALUES (?,?,?,?)",
                (message_id, json.dumps(cats) if cats is not None else None, (str(body.get("note") or "")[:500] or None), importer.now_iso()))
    return {"ok": True, "categories": cats}


# ------------------------------------------------------------ clasificación --

@app.get("/api/classify/runs")
def runs(chat_id: int, con=Depends(db)):
    active = search.latest_run(con, chat_id)
    return [{**dict(r), "config": json.loads(r["config"]), "active": r["id"] == active} for r in
            con.execute("SELECT * FROM analysis_runs WHERE chat_id=? ORDER BY id DESC", (chat_id,))]


@app.post("/api/classify/runs/{run_id}/activate")
def activate_run(run_id: int, con=Depends(db)):
    """Elige qué ejecución se muestra como clasificación vigente del chat. No borra ninguna."""
    r = con.execute("SELECT chat_id FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Ejecución no encontrada.")
    try:
        search.set_active_run(con, r["chat_id"], run_id)
    except KeyError:
        raise HTTPException(422, "Solo se puede activar una ejecución completada.")
    return {"ok": True, "active_run_id": run_id}


@app.post("/api/classify/runs")
def start_run(body: dict = Body(...), con=Depends(db)):
    chat_id = int(body.get("chat_id", 0))
    _chat(con, chat_id)
    try:
        res = classify.run(con, chat_id, str(body.get("provider", "rules")), _date(body.get("from")), _date(body.get("to")),
                           consent=body.get("consent") is True)
    except classify.ProviderError as e:
        raise HTTPException(400, str(e))
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
    res = search.category_monthly(con, chat_id, _date(date_from), _date(date_to), run_id)
    # Marca de mes parcial con el mismo criterio que las estadísticas oficiales.
    partial = {m["month"]: m for m in stats.compute(con, chat_id, _date(date_from), _date(date_to))["monthly"]}
    for m in res["months"]:
        info = partial.get(m["month"])
        m["partial"] = bool(info and info["partial"])
    return res


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


# ----------------------------------------------------------------- frontend --

@app.get("/como-funciona")
def how_it_works():
    """Diagrama de flujo de datos generado con Archify (HTML autocontenido, sin datos de chats).
    Lleva scripts en línea, así que tiene su propia política: sin red y sin acceso a la API."""
    f = ROOT / "docs" / "diagramas" / "flujo-de-datos.html"
    if not f.exists():
        raise HTTPException(404, "Diagrama no generado; ver README.")
    return FileResponse(f, media_type="text/html; charset=utf-8", headers={"Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
        "font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'")})


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
