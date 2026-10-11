"""Pruebas de extremo a extremo por HTTP, sin Jev y sin red."""
import re
from pathlib import Path

import pytest
from conftest import api_client, fixture_bytes

WEB = Path(__file__).parent.parent / "web"


@pytest.fixture
def client():
    with api_client() as c:
        yield c


def upload(client, name, **params):
    return client.post("/api/imports/preview", params={"filename": name, **params}, content=fixture_bytes(name))


def test_full_flow_without_jev(client):
    cfg = client.get("/api/config").json()
    assert cfg["loopback"] and cfg["default_timezone"] == "America/Costa_Rica"
    assert {p["name"]: p["available"] for p in cfg["providers"]} == {"rules": True, "jev": False, "openai_decisions": False}

    pv = upload(client, "ios_mdy_24h.txt").json()
    d = pv["diagnostics"]
    assert pv["date_order"] == "MDY" and d["records_detected"] == 18 and d["participant_messages"] == 13
    assert d["system_notices"] == 4 and d["unrecognized"] == 1 and d["period_start"].startswith("2026-01-14")
    assert any("adjuntos" in w for w in d["warnings"])
    r = client.post("/api/imports/commit", json={"token": pv["token"], "file_name": "ios_mdy_24h.txt",
                                                  "timezone": "America/Costa_Rica", "date_order": "MDY"}).json()
    cid = r["chat_id"]
    assert r["status"] == "imported" and client.get("/api/chats").json()[0]["participant_messages"] == 13

    assert client.get("/api/stats", params={"chat_id": cid}).json()["totals"]["active_authors"] == 3
    assert client.get("/api/stats", params={"chat_id": cid, "from": "2026-02-01"}).json()["totals"]["participant_messages"] == 8
    assert client.get("/api/stats", params={"chat_id": cid, "from": "ayer"}).status_code == 422

    hit = client.get("/api/search", params={"chat_id": cid, "q": "meetup"}).json()["items"][0]
    ctx = client.get(f"/api/messages/{hit['id']}/context", params={"before": 2, "after": 2}).json()
    assert len(ctx["items"]) == 5 and ctx["items"][2]["id"] == hit["id"]

    run = client.post("/api/classify/runs", json={"chat_id": cid, "provider": "rules"}).json()
    assert run["status"] == "completed"
    jev = client.post("/api/classify/runs", json={"chat_id": cid, "provider": "jev", "consent": True})
    assert jev.status_code == 400 and "JEV_ENABLED" in jev.json()["detail"]
    assert client.post(f"/api/messages/{hit['id']}/labels", json={"categories": ["anuncio", "nope"]}).status_code == 422
    assert client.post(f"/api/messages/{hit['id']}/labels", json={"categories": ["anuncio"]}).status_code == 200
    labeled = [i for i in client.get("/api/search", params={"chat_id": cid, "category": "anuncio"}).json()["items"] if i["id"] == hit["id"]]
    assert labeled[0]["human"]["categories"] == ["anuncio"] and labeled[0]["auto"]                 # predicción original intacta

    for fmt, ctype in (("html", "text/html"), ("txt", "text/plain")):
        rep = client.get("/api/report", params={"chat_id": cid, "format": fmt, "download": "true"})
        assert rep.status_code == 200 and ctype in rep.headers["content-type"] and "attachment" in rep.headers["content-disposition"]
        assert "meetup" not in rep.text and "Mensajes de participantes" in rep.text

    assert client.delete(f"/api/chats/{cid}").json()["records_removed"] == 18
    assert client.get("/api/chats").json() == [] and client.get("/api/search", params={"q": "meetup"}).json()["total"] == 0


def test_ambiguous_date_flow_and_low_overlap_guard(client):
    pv = upload(client, "ambiguous.txt").json()
    assert pv["needs_date_order"] and pv["date_detection"]["status"] == "ambiguous"
    bad = client.post("/api/imports/commit", json={"token": pv["token"], "file_name": "ambiguous.txt"})
    assert bad.status_code == 422 and client.get("/api/chats").json() == []
    pv = upload(client, "ambiguous.txt", date_order="DMY").json()
    assert not pv["needs_date_order"] and pv["diagnostics"]["period_start"].startswith("2026-04-03")

    a = upload(client, "overlap_a.txt").json()
    cid = client.post("/api/imports/commit", json={"token": a["token"], "file_name": "a.txt", "date_order": "MDY"}).json()["chat_id"]
    o = upload(client, "other_chat.txt").json()
    body = {"token": o["token"], "file_name": "o.txt", "date_order": "MDY", "chat_id": cid}
    r = client.post("/api/imports/commit", json=body)
    assert r.status_code == 409 and r.json()["error"] == "low_overlap"
    assert client.post("/api/imports/commit", json={**body, "confirm_low_overlap": True}).json()["status"] == "imported"


def test_content_is_served_as_data_not_markup(client):
    pv = upload(client, "ios_mdy_24h.txt").json()
    cid = client.post("/api/imports/commit", json={"token": pv["token"], "file_name": "x.txt", "date_order": "MDY"}).json()["chat_id"]
    r = client.get("/api/search", params={"chat_id": cid, "q": "script"})
    assert r.headers["content-type"].startswith("application/json")            # nunca text/html
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.json()["items"][0]["text"].startswith('<script>alert("xss")</script>')  # se conserva literal
    csp = client.get("/").headers["content-security-policy"]
    assert "default-src 'self'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp


def test_nothing_is_written_before_the_whole_request_is_valid(client):
    pv = upload(client, "overlap_a.txt").json()
    cid = client.post("/api/imports/commit", json={"token": pv["token"], "file_name": "a.txt", "date_order": "MDY", "chat_name": "Original"}).json()["chat_id"]
    before = client.get("/api/chats").json()[0]
    for body in ({"name": "Nuevo", "timezone": "Marte/Olympus"}, {"name": "   ", "timezone": "Europe/Madrid"},
                 {"name": "Nuevo", "timezone": 5}, {"name": ["Nuevo"]}, {"name": "Nuevo", "reinterpret_imports": "no"}):
        assert client.patch(f"/api/chats/{cid}", json=body).status_code == 422, body
        assert client.get("/api/chats").json()[0] == before, body          # ni el nombre ni la zona cambiaron
    ok = client.patch(f"/api/chats/{cid}", json={"name": " Nuevo ", "timezone": "Europe/Madrid"})
    assert ok.status_code == 200 and (ok.json()["name"], ok.json()["timezone"]) == ("Nuevo", "Europe/Madrid")
    assert client.patch(f"/api/chats/{cid}", json={}).json()["name"] == "Nuevo"


def test_malformed_bodies_are_rejected_with_422_never_500(client):
    pv = upload(client, "ios_mdy_24h.txt").json()
    cid = client.post("/api/imports/commit", json={"token": pv["token"], "file_name": "x.txt", "date_order": "MDY"}).json()["chat_id"]
    authors = client.get("/api/authors", params={"chat_id": cid}).json()["authors"]
    mid = client.get("/api/search", params={"chat_id": cid}).json()["items"][0]["id"]
    news = {"chat_id": cid, "from": "2026-01-01", "to": "2026-02-28"}
    bad = [
        ("/api/classify/runs", {"chat_id": None}), ("/api/classify/runs", {"chat_id": "x"}), ("/api/classify/runs", {}),
        ("/api/classify/runs", {"chat_id": True}), ("/api/classify/runs", {"chat_id": cid, "from": 20260101}),
        ("/api/classify/runs", {"chat_id": cid, "to": ["2026-01-01"]}), ("/api/classify/runs", {"chat_id": cid, "consent": "true"}),
        ("/api/classify/runs", {"chat_id": cid, "provider": None}), ("/api/classify/runs", [cid]),
        (f"/api/authors/{authors[1]['id']}/merge", {"into": "x"}), (f"/api/authors/{authors[1]['id']}/merge", {"into": None}),
        (f"/api/authors/{authors[1]['id']}/merge", {}),
        ("/api/imports/commit", {"token": None}), ("/api/imports/commit", {"token": pv["token"], "chat_id": "x"}),
        ("/api/imports/commit", {"token": pv["token"], "date_order": 3}), ("/api/imports/commit", {"token": pv["token"], "confirm_low_overlap": "si"}),
        (f"/api/messages/{mid}/labels", {"categories": "anuncio"}), (f"/api/messages/{mid}/labels", {"categories": [1]}),
        (f"/api/messages/{mid}/labels", {"categories": ["anuncio"], "note": 5}),
        ("/api/newspaper/editions", {**news, "chat_id": None}), ("/api/newspaper/editions", {**news, "chat_id": "x"}),
        ("/api/newspaper/editions", {**news, "max_articles": "muchos"}), ("/api/newspaper/editions", {**news, "max_articles": None}),
        ("/api/newspaper/editions", {**news, "readers": "anthropic"}), ("/api/newspaper/editions", {**news, "consent_chat": 1, "readers": ["anthropic"]}),
        ("/api/newspaper/editions", {**news, "order": "raro"}), ("/api/newspaper/editions", {**news, "from": 1}),
        ("/api/newspaper/editions", {**news, "to": None}), ("/api/newspaper/editions", {**news, "editor": None}),
        ("/api/newspaper/editions", {**news, "from": ""}), ("/api/newspaper/editions", {"chat_id": cid, "from": "2026-01-01"}),
        ("/api/newspaper/editions", {**news, "from": "2026-02-30"}), ("/api/newspaper/editions", {**news, "to": "ayer"}),
        ("/api/newspaper/editions", {**news, "from": "2026-03-01"}), ("/api/newspaper/editions", {**news, "max_articles": 50}),
        ("/api/newspaper/editions", {**news, "max_articles": 2}),
    ]
    for path, body in bad:
        r = client.post(path, json=body)
        assert r.status_code == 422, (path, body, r.status_code, r.text[:200])
    # Nada de lo anterior dejó rastro.
    assert client.get("/api/classify/runs", params={"chat_id": cid}).json() == []
    assert client.get("/api/newspaper/editions", params={"chat_id": cid}).json() == []
    assert all(not a["aliases"] for a in client.get("/api/authors", params={"chat_id": cid}).json()["authors"])
    assert client.get("/api/search", params={"chat_id": cid, "human_only": "true"}).json()["total"] == 0
    assert len(client.get("/api/chats").json()) == 1
    for params in ({"from": "2026-13-01"}, {"to": "1/2/26"}, {"from": "2026-02-01", "to": "2026-01-01"}, {"max_articles": 50},
                   {"max_articles": 2}, {"max_articles": "x"}, {"order": "raro"}):
        assert client.get("/api/newspaper/preview", params={"chat_id": cid, **params}).status_code == 422, params


FRONTEND = sorted((WEB / "src").rglob("*.ts")) + sorted((WEB / "dist").rglob("*.js"))


def test_frontend_never_builds_html_from_strings():
    """Se revisan tanto las fuentes TypeScript como el JavaScript compilado que sirve la app."""
    assert len(FRONTEND) >= 10
    for f in FRONTEND:
        code = re.sub(r"//.*", "", f.read_text(encoding="utf-8"))
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "srcdoc"):
            assert sink not in code, f"{f.name}: {sink}"
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "onclick=" not in html and "<script>" not in html and "http" not in html.replace("http-equiv", "")
    assert 'src="dist/app.js"' in html and not (WEB / "app.js").exists()


def test_only_state_module_changes_what_is_selected_or_cached():
    """Grupo, período y lo guardado en memoria se cambian solo desde state.ts (selectChat, setPeriod…):
    así ninguna vista puede dejar datos de otro grupo o de otro período a la vista."""
    for f in sorted((WEB / "src").rglob("*.ts")):
        code = re.sub(r"//.*", "", f.read_text(encoding="utf-8"))
        assert "state.authors" not in code, f.name
        if f.name != "state.ts":
            assert not re.search(r"\bstate\.(chatId|chats|from|to)\s*=(?!=)", code), f.name
    explorar = (WEB / "src" / "views" / "explorar.ts").read_text(encoding="utf-8")
    assert "chatAuthors()" in explorar and "getAuthors" not in explorar


def test_no_whatsapp_deep_links_are_invented():
    code = "".join(f.read_text(encoding="utf-8") for f in FRONTEND)
    assert "wa.me" not in code and "whatsapp://" not in code and "api.whatsapp.com" not in code
    assert "#/m/${m.id}" in code


def test_compiled_frontend_is_served(client):
    for name in ("app.js", "router.js", "api.js", "charts.js", "dom.js", "format.js", "views/resumen.js", "components/message.js"):
        r = client.get(f"/dist/{name}")
        assert r.status_code == 200 and "javascript" in r.headers["content-type"], name


def test_diagram_page_cannot_reach_network_or_api(client):
    for path in ("/como-funciona", "/como-funciona/periodico"):
        r = client.get(path)
        assert r.status_code == 200 and "Community Lens" in r.text, path
        csp = r.headers["content-security-policy"]
        assert "connect-src 'none'" in csp and "default-src 'none'" in csp
        # autocontenido: ningún recurso remoto
        assert not re.search(r'(?:src|href)="https?://(?!www\.w3\.org)', r.text), path
    assert client.get("/como-funciona/otro").status_code == 404
    # y la app principal conserva su política estricta
    assert "unsafe-inline" not in client.get("/").headers["content-security-policy"]


def test_chart_library_is_local_and_csp_stays_strict(client):
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'src="vendor/d3.min.js"' in html and 'src="vendor/plot.umd.min.js"' in html
    for f in ("d3.min.js", "plot.umd.min.js"):
        assert client.get(f"/vendor/{f}").status_code == 200
    csp = client.get("/").headers["content-security-policy"]
    assert "style-src 'self' 'sha256-" in csp and "unsafe-inline" not in csp and "script-src" not in csp
