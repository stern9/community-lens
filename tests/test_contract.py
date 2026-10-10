"""Contrato entre el backend (Python) y el frontend (TypeScript).

web/src/api-schema.json se genera desde web/src/types.ts (`npm run schema`). Aquí se validan las
respuestas REALES de la API contra ese esquema, sin tolerar campos de más ni de menos. Si alguien
cambia una respuesta del backend sin actualizar los tipos (o al revés), esta prueba falla.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import fixture_bytes
from fastapi.testclient import TestClient
from jsonschema import Draft7Validator

ROOT = Path(__file__).parent.parent
SCHEMA = json.loads((ROOT / "web" / "src" / "api-schema.json").read_text(encoding="utf-8"))


def check(type_name: str, data, *, array: bool = False) -> None:
    assert type_name in SCHEMA["definitions"], f"types.ts no define {type_name}"
    ref = {"$ref": f"#/definitions/{type_name}"}
    schema = {"definitions": SCHEMA["definitions"], **({"type": "array", "items": ref} if array else ref)}
    errors = sorted(Draft7Validator(schema).iter_errors(data), key=lambda e: list(e.absolute_path))
    assert not errors, f"{type_name}: " + "; ".join(f"{'/'.join(map(str, e.absolute_path)) or '(raíz)'}: {e.message[:160]}" for e in errors[:8])


@pytest.fixture
def client():
    from community_lens.api import app
    with TestClient(app) as c:
        yield c


def ok(resp):
    assert resp.status_code == 200, resp.text
    return resp.json()


def upload(client, name, **params):
    return client.post("/api/imports/preview", params={"filename": name, **params}, content=fixture_bytes(name))


def test_every_object_type_is_closed():
    """El esquema no admite campos desconocidos: un campo nuevo del backend debe declararse en types.ts."""
    open_types = [n for n, d in SCHEMA["definitions"].items()
                  if d.get("type") == "object" and "properties" in d and d.get("additionalProperties") is not False]
    assert open_types == []


def test_request_bodies_declare_the_same_fields_as_typescript():
    """Cada cuerpo de solicitud tiene los mismos campos en types.ts y en los modelos de api.py."""
    from community_lens import api
    for name in ("ChatUpdateRequest", "ImportCommitRequest", "MergeAuthorRequest", "LabelsRequest", "RunStartRequest", "NewspaperRequest"):
        model = getattr(api, name)
        fields = {f.alias or k for k, f in model.model_fields.items()}
        assert fields == set(SCHEMA["definitions"][name]["properties"]), name
        required = {f.alias or k for k, f in model.model_fields.items() if f.is_required()}
        assert required <= set(SCHEMA["definitions"][name].get("required", [])), name   # lo obligatorio para la API siempre viaja


def test_responses_match_typescript_types(client):
    check("Config", ok(client.get("/api/config")))
    check("Chat", ok(client.get("/api/chats")), array=True)

    # Importación: fecha ambigua (sin diagnóstico), detectada, confirmada y duplicada
    amb = ok(upload(client, "ambiguous.txt"))
    assert amb["diagnostics"] is None
    check("ImportPreview", amb)
    check("ImportPreview", ok(upload(client, "ambiguous.txt", date_order="DMY")))
    check("ImportPreview", ok(upload(client, "conflict.txt")))
    check("ImportPreview", ok(upload(client, "android_dmy.txt")))
    pv = ok(upload(client, "ios_mdy_24h.txt"))
    check("ImportPreview", pv)
    body = {"token": pv["token"], "file_name": "ios_mdy_24h.txt", "chat_id": None, "chat_name": "Demo",
            "timezone": "America/Costa_Rica", "date_order": "MDY", "confirm_low_overlap": False}
    check("ImportCommitRequest", body)
    done = ok(client.post("/api/imports/commit", json=body))
    check("ImportCommitImported", done)
    check("ImportCommitResult", done)
    cid = done["chat_id"]
    again = ok(upload(client, "ios_mdy_24h.txt"))
    check("ImportPreview", again)                      # ahora con candidatos y recomendación "duplicate"
    dup = ok(client.post("/api/imports/commit", json={**body, "token": again["token"], "chat_id": cid}))
    check("ImportCommitDuplicate", dup)
    third = ok(upload(client, "third_party_12h.txt"))  # trae una línea sin fecha → issues
    assert third["diagnostics"]["issues"]
    check("ImportPreview", third)

    check("Chat", ok(client.get("/api/chats")), array=True)
    check("ImportRecord", ok(client.get(f"/api/chats/{cid}/imports")), array=True)
    zone = {"timezone": "America/Costa_Rica", "reinterpret_imports": True}
    check("ChatUpdateRequest", zone)
    check("ChatRow", ok(client.patch(f"/api/chats/{cid}", json=zone)))

    # Estadísticas: con datos, con filtro y período vacío (promedios y hora pico en null)
    for params in ({}, {"from": "2026-02-01"}, {"from": "2030-01-01"}):
        check("Stats", ok(client.get("/api/stats", params={"chat_id": cid, **params})))
        check("AuthorsResult", ok(client.get("/api/authors", params={"chat_id": cid, **params})))

    # Clasificación: antes y después de la primera ejecución
    check("CategorySummary", ok(client.get("/api/classify/summary", params={"chat_id": cid})))
    check("CategoryMonthly", ok(client.get("/api/classify/monthly", params={"chat_id": cid})))
    check("RunListItem", ok(client.get("/api/classify/runs", params={"chat_id": cid})), array=True)
    start = {"chat_id": cid, "from": "", "to": "", "provider": "rules", "consent": False}
    check("RunStartRequest", start)
    run = ok(client.post("/api/classify/runs", json=start))
    check("RunStartResult", run)
    check("CategorySummary", ok(client.get("/api/classify/summary", params={"chat_id": cid})))
    check("CategoryMonthly", ok(client.get("/api/classify/monthly", params={"chat_id": cid})))
    check("RunListItem", ok(client.get("/api/classify/runs", params={"chat_id": cid})), array=True)
    check("ActivateRunResult", ok(client.post(f"/api/classify/runs/{run['run_id']}/activate", json={})))

    # Búsqueda y contexto: con texto (snippet), sin texto, avisos del sistema y registros por revisar
    hit = ok(client.get("/api/search", params={"chat_id": cid, "q": "meetup"}))
    assert "snippet" in hit["items"][0]
    check("SearchResult", hit)
    for params in ({}, {"record_type": "system"}, {"record_type": "all", "review_only": "true"}, {"has_link": "true"}, {"q": "zzzz"}):
        check("SearchResult", ok(client.get("/api/search", params={"chat_id": cid, **params})))
    mid = hit["items"][0]["id"]
    check("ContextResult", ok(client.get(f"/api/messages/{mid}/context", params={"before": 20, "after": 20})))

    # Recursos: documentos (ninguno en este fixture) y enlaces
    for params in ({"kind": "document"}, {"kind": "link"}, {"kind": "link", "q": "example", "order": "shared"}):
        check("ResourceLibrary", ok(client.get("/api/resources", params={"chat_id": cid, **params})))

    # Correcciones humanas: etiquetar y revertir
    check("LabelsRequest", {"categories": ["anuncio"]})
    check("LabelsRequest", {"categories": None})
    check("LabelsResult", ok(client.post(f"/api/messages/{mid}/labels", json={"categories": ["anuncio"]})))
    labeled = ok(client.get("/api/search", params={"chat_id": cid, "human_only": "true"}))
    assert labeled["items"][0]["human"]["categories"] == ["anuncio"]
    check("SearchResult", labeled)
    check("CategorySummary", ok(client.get("/api/classify/summary", params={"chat_id": cid})))
    check("LabelsResult", ok(client.post(f"/api/messages/{mid}/labels", json={"categories": None})))

    # Alias
    authors = ok(client.get("/api/authors", params={"chat_id": cid}))["authors"]
    check("MergeAuthorRequest", {"into": authors[0]["id"]})
    check("OkResult", ok(client.post(f"/api/authors/{authors[1]['id']}/merge", json={"into": authors[0]["id"]})))
    merged = ok(client.get("/api/authors", params={"chat_id": cid}))
    assert merged["authors"][0]["aliases"]
    check("AuthorsResult", merged)
    check("SearchResult", ok(client.get("/api/search", params={"chat_id": cid})))   # canonical_label presente
    check("OkResult", ok(client.post(f"/api/authors/{authors[1]['id']}/unmerge", json={})))

    # Errores
    bad = client.get("/api/stats", params={"chat_id": 999})
    assert bad.status_code == 404
    check("ApiErrorBody", bad.json())
    a = ok(upload(client, "overlap_a.txt"))
    other = ok(client.post("/api/imports/commit", json={**body, "token": a["token"], "chat_name": "A"}))["chat_id"]
    o = ok(upload(client, "other_chat.txt"))
    low = client.post("/api/imports/commit", json={**body, "token": o["token"], "chat_id": other})
    assert low.status_code == 409 and low.json()["error"] == "low_overlap"
    check("ApiErrorBody", low.json())

    # Recursos con documentos versionados (el fixture de terceros trae <document omitted>)
    docs = (b"[6/1/26, 9:00:00 AM] +506 5555 0202: <document omitted> Manual-Demo-v1.4.docx\n"
            b"[6/2/26, 9:00:00 AM] +506 5555 0303: <document omitted> Manual-Demo-v1_6.pdf\n")
    dv = ok(client.post("/api/imports/preview", params={"filename": "docs.txt"}, content=docs))
    dcid = ok(client.post("/api/imports/commit", json={**body, "token": dv["token"], "chat_name": "Docs"}))["chat_id"]
    lib = ok(client.get("/api/resources", params={"chat_id": dcid}))
    assert lib["items"][0]["latest"]["version"] == "1.6"
    check("ResourceLibrary", lib)

    check("DeleteChatResult", ok(client.delete(f"/api/chats/{cid}")))


def test_jev_run_and_provider_info_match_types(client, monkeypatch):
    """Las formas propias de Jev (puntuaciones, configuración, campos extra del proveedor)."""
    from community_lens import classify
    from community_lens.classify import jev
    from community_lens.classify.taxonomy import CATEGORY_KEYS
    from community_lens.config import settings
    from community_lens.db import connect
    monkeypatch.setenv("JEV_ENABLED", "1"); monkeypatch.setenv("TYPESAFE_API_KEY", "k"); settings.cache_clear()
    cfg = ok(client.get("/api/config"))
    assert next(p for p in cfg["providers"] if p["name"] == "jev")["available"]
    check("Config", cfg)
    pv = ok(upload(client, "overlap_a.txt"))
    cid = ok(client.post("/api/imports/commit", json={"token": pv["token"], "file_name": "a.txt", "date_order": "MDY"}))["chat_id"]
    stub = jev.JevProvider(lambda p: {"model": "jev-x", "answers": {k: {"type": "noul", "noul": 0.9} for k in CATEGORY_KEYS}})
    con = connect()
    check("RunStartResult", classify.run(con, cid, "jev", consent=True, provider=stub))
    con.close()
    items = ok(client.get("/api/search", params={"chat_id": cid}))
    assert items["items"][0]["auto"][0]["score"] == 0.9
    check("SearchResult", items)
    check("RunListItem", ok(client.get("/api/classify/runs", params={"chat_id": cid})), array=True)
    check("CategorySummary", ok(client.get("/api/classify/summary", params={"chat_id": cid})))


@pytest.mark.skipif(not (ROOT / "node_modules" / ".bin" / "tsc").exists(), reason="requiere `npm install` (solo para desarrollar el frontend)")
def test_typescript_compiles_and_committed_build_is_current(tmp_path):
    """Chequeo estricto de tipos, y web/dist + api-schema.json al día con web/src."""
    tsc = str(ROOT / "node_modules" / ".bin" / "tsc")
    r = subprocess.run([tsc, "-p", "tsconfig.json", "--outDir", str(tmp_path / "dist")], cwd=ROOT, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    names = lambda d: sorted(str(p.relative_to(d)) for p in d.rglob("*.js"))
    built = names(tmp_path / "dist")
    assert built == names(ROOT / "web" / "dist"), "web/dist tiene archivos de más o de menos: corré `npm run build`"
    for name in built:
        assert (tmp_path / "dist" / name).read_text() == (ROOT / "web" / "dist" / name).read_text(), f"web/dist/{name} está desactualizado: corré `npm run build`"
    gen = ROOT / "node_modules" / ".bin" / "ts-json-schema-generator"
    if gen.exists() and shutil.which("node"):
        out = tmp_path / "schema.json"
        r = subprocess.run([str(gen), "--path", "web/src/types.ts", "--tsconfig", "tsconfig.json", "--type", "*", "--no-type-check", "-o", str(out)],
                           cwd=ROOT, capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stdout + r.stderr
        assert json.loads(out.read_text()) == SCHEMA, "web/src/api-schema.json está desactualizado: corré `npm run schema`"


@pytest.mark.skipif(not shutil.which("node"), reason="requiere Node (solo para desarrollar el frontend)")
def test_newspaper_view_completes_cleared_dates():
    """La vista compilada pide otra vista previa al borrar una fecha y genera con el rango resuelto."""
    r = subprocess.run(["node", "--experimental-vm-modules", "tests/frontend/periodico.mjs"], cwd=ROOT,
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
