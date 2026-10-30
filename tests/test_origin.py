"""De dónde viene una solicitud. La app no tiene autenticación: lo único que la separa de una página abierta en
el mismo navegador es que solo responde a su propia dirección (Host) y que no modifica nada si la solicitud
declara venir de otro sitio (Origin, Sec-Fetch-Site). Ver docs/seguridad.md, "Red"."""
import asyncio
import json
import re

import pytest
from conftest import BASE_URL, FIX, FakeLLM, FakeNet, api_client, fixture_bytes
from fastapi.routing import APIRoute
from starlette.routing import Mount

from community_lens import llm, newspaper
from community_lens.api import app
from community_lens.config import settings
from community_lens.db import connect
from community_lens.newspaper import edition

OWN = BASE_URL.removeprefix("http://")          # 127.0.0.1:8765
EVIL = "evil.example"
SAFE, UNSAFE = ("GET", "HEAD", "OPTIONS"), ("POST", "PUT", "PATCH", "DELETE")


def raw(method, path, headers, body=b""):
    """Una solicitud con los encabezados EXACTOS que se le pasan (pares, para poder repetir u omitir Host), sin
    cliente HTTP de por medio. `server` es la dirección real: si falta Host, la app no debe conformarse con ella."""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers],
             "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8765)}
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    text = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body").decode()
    return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, text


def assert_rejected(resp, status, code):
    """El rechazo tiene la forma de error de la API (ApiErrorBody en types.ts), en JSON y con las cabeceras de siempre."""
    got, headers, text = resp
    assert got == status, (got, text[:200])
    body = json.loads(text)
    assert set(body) == {"error", "detail"} and body["error"] == code and isinstance(body["detail"], str) and body["detail"]
    assert headers["content-type"] == "application/json" and headers["x-content-type-options"] == "nosniff"
    assert headers["cache-control"] == "no-store" and EVIL not in text      # no se repite lo que mandó el otro lado


# -------------------------------------------------------------------- Host --

@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "[::1]:8765", "LOCALHOST:8765", "LocalHost:8765"])
def test_own_addresses_are_served(host):
    for path in ("/api/config", "/", "/dist/app.js"):
        assert raw("GET", path, [("Host", host)])[0] == 200, (host, path)


@pytest.mark.parametrize("host", [
    EVIL, f"{EVIL}:8765", "EVIL.EXAMPLE:8765",
    "127.0.0.1", "localhost", "[::1]",                                    # sin puerto: sería el 80, que no es el de la app
    "127.0.0.1:8766", "localhost:80", "[::1]:9999", "127.0.0.1:", "127.0.0.1:08765", "127.0.0.1:8765:8765",
    "localhost.:8765", "127.0.0.1.:8765", "localhost.", "127.0.0.1.",     # con punto final
    "127.0.0.1.evil.example:8765", "127.0.0.1.evil.example", "localhost.evil.example:8765", "evil.localhost:8765",
    "evil-127.0.0.1:8765", "127.0.0.1:8765.evil.example", "127.0.0.1:8765@evil.example", "evil.example@127.0.0.1:8765",
    "::1:8765", "[::1]8765", "[::1", "[::1]:8765.evil.example", "[0:0:0:0:0:0:0:1]:8765",
    "0.0.0.0:8765", "192.168.1.20:8765", "2130706433:8765", "127.1:8765", "0x7f.0.0.1:8765",
    "", " ", "*", "127.0.0.1:8765, evil.example", "127.0.0.1:8765 evil.example", "localhost:8765/", "http://127.0.0.1:8765",
    "locálhost:8765",
])
def test_any_other_host_is_refused_everywhere(host):
    for path in ("/api/config", "/api/search", "/", "/dist/app.js", "/vendor/d3.min.js", "/como-funciona", "/periodico/1", "/no-existe"):
        assert_rejected(raw("GET", path, [("Host", host)]), 421, "host_not_allowed")


def test_missing_or_repeated_host_is_refused():
    assert_rejected(raw("GET", "/api/config", []), 421, "host_not_allowed")
    assert_rejected(raw("GET", "/api/config", [("Accept", "*/*")]), 421, "host_not_allowed")
    for pair in ((OWN, EVIL), (EVIL, OWN), (OWN, OWN), (OWN, "")):
        assert_rejected(raw("GET", "/api/config", [("Host", h) for h in pair]), 421, "host_not_allowed")


def test_forwarded_headers_change_nothing():
    for name in ("X-Forwarded-Host", "Forwarded", "X-Host", "X-Forwarded-Server", "X-Original-Host"):
        own = OWN if name != "Forwarded" else f"host={OWN}"
        assert_rejected(raw("GET", "/api/config", [("Host", EVIL), (name, own)]), 421, "host_not_allowed")
        evil = EVIL if name != "Forwarded" else f"host={EVIL}"
        assert raw("GET", "/api/config", [("Host", OWN), (name, evil)])[0] == 200
    # Ni haciéndose pasar por un proxy con TLS: el origen propio sigue siendo http.
    spoof = [("Host", OWN), ("X-Forwarded-Proto", "https"), ("Origin", f"https://{OWN}")]
    assert_rejected(raw("POST", "/api/authors/999/unmerge", spoof), 403, "cross_origin")


def test_the_configured_port_is_the_only_one(setenv):
    setenv(CL_PORT="9000")
    assert raw("GET", "/api/config", [("Host", "127.0.0.1:9000")])[0] == 200
    assert_rejected(raw("GET", "/api/config", [("Host", OWN)]), 421, "host_not_allowed")
    setenv(CL_PORT="80")          # el único puerto que el navegador omite
    for host in ("127.0.0.1", "localhost", "[::1]", "127.0.0.1:80"):
        assert raw("GET", "/api/config", [("Host", host)])[0] == 200, host
        assert raw("POST", "/api/authors/999/unmerge", [("Host", host), ("Origin", "http://" + host.removesuffix(":80"))])[0] == 200, host
    assert_rejected(raw("GET", "/api/config", [("Host", OWN)]), 421, "host_not_allowed")
    assert_rejected(raw("GET", "/api/config", [("Host", EVIL)]), 421, "host_not_allowed")


def test_exposed_instance_accepts_ip_addresses_and_listed_names_only(setenv):
    """Con CL_HOST fuera de loopback no se puede saber qué dirección va a usar el dueño, pero sí que una dirección IP
    escrita tal cual no puede ser el nombre de otro; los nombres se aceptan solo si están en CL_ALLOWED_HOSTS."""
    ips = ("192.168.1.20:8765", "10.0.0.7:8765", "[fe80::1]:8765", "[2001:db8::5]:8765")
    for h in ips:
        assert_rejected(raw("GET", "/api/config", [("Host", h)]), 421, "host_not_allowed")       # en loopback, no
    for cl_host in ("0.0.0.0", "::", "192.168.1.20"):
        setenv(CL_HOST=cl_host)
        for h in (*ips, OWN, "localhost:8765", "[::1]:8765"):
            assert raw("GET", "/api/config", [("Host", h)])[0] == 200, (cl_host, h)
            assert raw("POST", "/api/authors/999/unmerge", [("Host", h), ("Origin", f"http://{h}")])[0] == 200, (cl_host, h)
            assert_rejected(raw("POST", "/api/authors/999/unmerge", [("Host", h), ("Origin", f"http://{EVIL}")]), 403, "cross_origin")
        assert_rejected(raw("POST", "/api/authors/999/unmerge", [("Host", ips[0]), ("Origin", f"http://{ips[1]}")]), 403, "cross_origin")
        for h in (EVIL, f"{EVIL}:8765", "mi-equipo.local:8765", "192.168.1.20", "192.168.1.20:9999", "192.168.1.20.evil.example:8765",
                  "192.168.1.20.:8765", "[fe80::1%25en0]:8765", "[fe80::1%en0]:8765", "[192.168.1.20]:8765", "fe80::1:8765", "192.168.1:8765"):
            assert_rejected(raw("GET", "/api/config", [("Host", h)]), 421, "host_not_allowed")
    setenv(CL_HOST="0.0.0.0", CL_ALLOWED_HOSTS=" Mi-Equipo.local , otro.example,, ")
    for h in ("mi-equipo.local:8765", "MI-EQUIPO.LOCAL:8765", "otro.example:8765"):
        assert raw("GET", "/api/config", [("Host", h)])[0] == 200, h
    for h in ("mi-equipo.local", "mi-equipo.local:80", "mi-equipo.local.:8765", "x.mi-equipo.local:8765", "mi-equipo.local.evil.example:8765", EVIL, ":8765", ""):
        assert_rejected(raw("GET", "/api/config", [("Host", h)]), 421, "host_not_allowed")
    setenv(CL_HOST="mi-equipo.local", CL_ALLOWED_HOSTS="")       # un nombre en CL_HOST es también una dirección propia
    assert raw("GET", "/api/config", [("Host", "mi-equipo.local:8765")])[0] == 200
    assert_rejected(raw("GET", "/api/config", [("Host", "otro.example:8765")]), 421, "host_not_allowed")
    # Un comodín o una lista mal escrita no abren nada.
    for value in ("*", "*.example", "evil.example:8765", "http://evil.example", "evil.example/"):
        setenv(CL_HOST="127.0.0.1", CL_ALLOWED_HOSTS=value)
        for h in (EVIL, f"{EVIL}:8765", "x.example:8765", "*:8765", f"{value}:8765", value):
            assert_rejected(raw("GET", "/api/config", [("Host", h)]), 421, "host_not_allowed")
        assert raw("GET", "/api/config", [("Host", OWN)])[0] == 200


# ------------------------------------------------------------------ Origin --

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(newspaper, "NET", FakeNet(lambda url: (404, {}, "")))      # por si algo llegara a ejecutarse
    monkeypatch.setattr(llm, "TRANSPORT", FakeLLM(lambda *a: 500))
    with api_client() as c:
        yield c


@pytest.fixture
def seeded(client):
    """Una base donde cada ruta que modifica tendría algo que cambiar: un alias unido, dos ejecuciones terminadas
    (la vigente es la segunda), una vista previa de importación sin confirmar y una edición del periódico en borrador
    (aprobarla, descartarla y recuperarla la cambian, en ese orden)."""
    pv = client.post("/api/imports/preview", params={"filename": "ios_mdy_24h.txt"}, content=fixture_bytes("ios_mdy_24h.txt")).json()
    cid = client.post("/api/imports/commit", json={"token": pv["token"], "file_name": "x.txt", "date_order": "MDY"}).json()["chat_id"]
    authors = client.get("/api/authors", params={"chat_id": cid}).json()["authors"]
    main, alias, third = (a["id"] for a in authors[:3])
    assert client.post(f"/api/authors/{alias}/merge", json={"into": main}).status_code == 200
    first = client.post("/api/classify/runs", json={"chat_id": cid, "provider": "rules"}).json()["run_id"]
    second = client.post("/api/classify/runs", json={"chat_id": cid, "provider": "rules"}).json()["run_id"]
    assert [r["id"] for r in client.get("/api/classify/runs", params={"chat_id": cid}).json() if r["active"]] == [second]
    mid = client.get("/api/search", params={"chat_id": cid}).json()["items"][0]["id"]
    token = client.post("/api/imports/preview", params={"filename": "overlap_a.txt"}, content=fixture_bytes("overlap_a.txt")).json()["token"]
    v1 = json.loads((FIX / "edition_v1.json").read_text(encoding="utf-8"))
    data = edition.upgrade(v1["data"])
    con = connect()
    try:
        eid = con.execute("""INSERT INTO editions(chat_id, date_from, date_to, created_at, editor, sent, data, html, status)
                             VALUES (?,?,?,?,?,?,?,'','borrador')""",
                          (cid, data["from"], data["to"], "2026-10-10T18:00:00Z", json.dumps(data["editor"]), json.dumps(v1["sent"]),
                           json.dumps(data, ensure_ascii=False))).lastrowid
    finally:
        con.close()
    return {"chat_id": cid, "author_id": alias, "into": main, "third": third, "run_id": first, "message_id": mid, "token": token,
            "edition_id": eid}


def snapshot():
    """Todo lo que la app guarda: cada fila de cada tabla y los archivos de vistas previas."""
    con = connect()
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        rows = {t: sorted(repr(tuple(r)) for r in con.execute(f'SELECT * FROM "{t}"')) for t in tables}
    finally:
        con.close()
    return rows, sorted(f.name for f in settings().staging_dir.iterdir())


def alias_is_merged(s):
    con = connect()
    try:
        return con.execute("SELECT canonical_id FROM authors WHERE id=?", (s["author_id"],)).fetchone()[0] == s["into"]
    finally:
        con.close()


def test_post_without_origin_still_works(client, seeded):
    """curl, scripts y estas pruebas: sin Origin ni Sec-Fetch-Site no hay nada que indique otro sitio."""
    assert alias_is_merged(seeded)
    assert raw("POST", f"/api/authors/{seeded['author_id']}/unmerge", [("Host", OWN)])[0] == 200
    assert not alias_is_merged(seeded)


@pytest.mark.parametrize("host, extra", [
    (OWN, [("Origin", f"http://{OWN}")]),                                    # lo que manda el frontend de la app
    (OWN, [("Origin", f"HTTP://{OWN}")]),
    ("localhost:8765", [("Origin", "http://localhost:8765")]),
    ("LOCALHOST:8765", [("Origin", "http://localhost:8765")]),
    ("[::1]:8765", [("Origin", "http://[::1]:8765")]),
    (OWN, [("Origin", f"http://{OWN}"), ("Sec-Fetch-Site", "same-origin")]),
    (OWN, [("Sec-Fetch-Site", "same-origin")]),
    (OWN, [("Sec-Fetch-Site", "none")]),                                     # la persona lo pidió directamente
    (OWN, [("Sec-Fetch-Site", "Same-Origin")]),
])
def test_requests_from_the_app_itself_can_modify(client, seeded, host, extra):
    assert raw("POST", f"/api/authors/{seeded['author_id']}/unmerge", [("Host", host), *extra])[0] == 200
    assert not alias_is_merged(seeded)


@pytest.mark.parametrize("extra", [
    [("Origin", f"https://{EVIL}")], [("Origin", f"http://{EVIL}")], [("Origin", f"http://{EVIL}:8765")],
    [("Origin", "http://127.0.0.1:8766")], [("Origin", "http://127.0.0.1")], [("Origin", "http://127.0.0.1:80")],   # otro puerto
    [("Origin", f"https://{OWN}")],                                          # la app sirve http
    [("Origin", "null")], [("Origin", "NULL")], [("Origin", "")], [("Origin", " ")],
    [("Origin", f"http://{OWN}/")], [("Origin", f"http://{OWN}/api")], [("Origin", f"http://{OWN}?x")], [("Origin", f"http://{OWN}#x")],
    [("Origin", "http://localhost:8765")], [("Origin", "http://[::1]:8765")],   # otra dirección de la app: no es el mismo origen
    [("Origin", f"http://{OWN}.evil.example")], [("Origin", f"http://{OWN}@evil.example")], [("Origin", f"http://evil.example@{OWN}")],
    [("Origin", OWN)], [("Origin", f"//{OWN}")], [("Origin", f"ws://{OWN}")], [("Origin", f"http://{OWN}, http://{EVIL}")],
    [("Origin", f"http://{OWN} http://{EVIL}")],
    [("Origin", f"http://{OWN}"), ("Origin", f"http://{EVIL}")], [("Origin", f"http://{EVIL}"), ("Origin", f"http://{OWN}")],
    [("Origin", f"http://{OWN}"), ("Origin", f"http://{OWN}")],
    [("Sec-Fetch-Site", "cross-site")], [("Sec-Fetch-Site", "same-site")], [("Sec-Fetch-Site", "CROSS-SITE")],
    [("Sec-Fetch-Site", "otro")], [("Sec-Fetch-Site", "")],
    [("Sec-Fetch-Site", "same-origin"), ("Sec-Fetch-Site", "cross-site")], [("Sec-Fetch-Site", "same-origin, cross-site")],
    [("Origin", f"http://{OWN}"), ("Sec-Fetch-Site", "cross-site")], [("Origin", f"http://{OWN}"), ("Sec-Fetch-Site", "same-site")],
    [("Origin", f"http://{EVIL}"), ("Sec-Fetch-Site", "same-origin")], [("Origin", "null"), ("Sec-Fetch-Site", "none")],
])
def test_requests_from_elsewhere_cannot_modify(client, seeded, extra):
    before = snapshot()
    for method in UNSAFE:
        for path in (f"/api/authors/{seeded['author_id']}/unmerge", f"/api/chats/{seeded['chat_id']}", "/", "/dist/app.js", "/no-existe"):
            assert_rejected(raw(method, path, [("Host", OWN), *extra]), 403, "cross_origin")
    assert alias_is_merged(seeded) and snapshot() == before


def test_reads_are_not_refused_by_origin(client, seeded):
    """GET y HEAD no modifican nada y un enlace a la app desde otra página es legítimo. Que otra página no pueda
    LEER la respuesta lo garantiza el navegador (la app no envía cabeceras CORS) y, si se hace pasar por el
    mismo origen cambiando el DNS, el Host."""
    before = snapshot()
    for extra in ([("Origin", f"https://{EVIL}")], [("Origin", "null")], [("Sec-Fetch-Site", "cross-site")],
                  [("Origin", f"https://{EVIL}"), ("Sec-Fetch-Site", "cross-site")]):
        for method in ("GET", "HEAD"):
            for path in ("/api/config", "/api/chats", "/", "/dist/app.js"):
                status, headers, _ = raw(method, path, [("Host", OWN), *extra])
                assert status not in (403, 421) and (method == "HEAD" or status == 200), (method, path, extra)
                assert not [h for h in headers if h.startswith("access-control-")]
        # La consulta previa de CORS no concede nada: sin ella el navegador no envía PATCH, DELETE ni JSON.
        preflight = [("Host", OWN), *extra, ("Access-Control-Request-Method", "DELETE"), ("Access-Control-Request-Headers", "content-type")]
        status, headers, _ = raw("OPTIONS", f"/api/chats/{seeded['chat_id']}", preflight)
        assert status == 405 and not [h for h in headers if h.startswith("access-control-")]
    assert snapshot() == before


# -------------------------------------------------- todas las rutas, sin lista --

# Cuerpos con los que cada ruta haría su trabajo si nadie la frenara. Es opcional: una ruta que no esté aquí se
# prueba igual, con la solicitud vacía (y si no lleva cuerpo, esa ya es la solicitud que la ejecuta).
def attack_bodies(s):
    return {
        "/api/chats/{chat_id}": {"json": {"name": "Cambiado"}},
        "/api/imports/preview": {"params": {"filename": "other_chat.txt"}, "content": fixture_bytes("other_chat.txt")},
        "/api/imports/commit": {"json": {"token": s["token"], "file_name": "a.txt", "date_order": "MDY"}},
        "/api/authors/{author_id}/merge": {"json": {"into": s["third"]}},
        "/api/messages/{message_id}/labels": {"json": {"categories": ["anuncio"]}},
        "/api/classify/runs": {"json": {"chat_id": s["chat_id"], "provider": "rules"}},
        "/api/newspaper/editions/{edition_id}/approve": {"json": {"by": "Jaime", "via": "WhatsApp"}},
    }


def registered(s):
    """(plantilla, ruta concreta, métodos registrados) de cada ruta de la app, con identificadores que existen.
    Sale de app.routes: una ruta nueva entra sola, y un tipo de ruta que esta prueba no conoce la hace fallar."""
    out = []
    for r in app.routes:
        if isinstance(r, APIRoute):
            out.append((r.path, re.sub(r"\{(\w+)\}", lambda m: str(s.get(m[1], 1)), r.path), set(r.methods)))
        elif isinstance(r, Mount):
            out += [(r.path + "/" + f, r.path + "/" + f, {"GET", "HEAD"}) for f in ("index.html", "dist/app.js", "vendor/d3.min.js", "no-existe.js")]
        else:
            pytest.fail(f"Ruta de un tipo que esta prueba no cubre: {r!r}")
    return out


def test_every_registered_route_checks_where_the_request_comes_from(client, seeded):
    routes, bodies = registered(seeded), attack_bodies(seeded)
    templates = {t for t, _, _ in routes}
    assert len(routes) > 25 and {"/", "/api/search", "/periodico/{edition_id}", "/como-funciona/{name}"} <= templates
    assert set(bodies) <= templates, "attack_bodies nombra rutas que ya no existen"
    unsafe = [(t, p, m) for t, p, methods in routes for m in sorted(methods - set(SAFE))]
    assert {(t, m) for t, _, m in unsafe} >= {("/api/authors/{author_id}/unmerge", "POST"), ("/api/classify/runs/{run_id}/activate", "POST"),
                                              ("/api/chats/{chat_id}", "DELETE"), ("/api/chats/{chat_id}", "PATCH"), ("/api/imports/preview", "POST")}
    before = snapshot()
    assert len(before[0]["messages"]) > 0 and len(before[0]["analysis_runs"]) == 2 and len(before[1]) == 1

    # 1. Con el Host de otro, nada responde: ni la API, ni las páginas, ni los archivos, con ningún método.
    for template, path, _ in routes:
        for method in (*SAFE, *UNSAFE):
            for headers in ({"Host": EVIL}, {"Host": f"{EVIL}:8765"}, {"Host": EVIL, "Origin": f"http://{EVIL}"}):
                r = client.request(method, path, headers=headers, **(bodies.get(template, {}) if method in UNSAFE else {}))
                assert r.status_code == 421 and (method == "HEAD" or r.json()["error"] == "host_not_allowed"), (method, path, r.status_code)
    assert snapshot() == before

    # 2. Con el Host correcto pero viniendo de otro sitio, ninguna ruta modifica nada.
    for template, path, _ in routes:
        for method in UNSAFE:
            for headers in ({"Origin": f"https://{EVIL}"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}):
                r = client.request(method, path, headers=headers, **bodies.get(template, {}))
                assert r.status_code == 403 and r.json()["error"] == "cross_origin", (method, path, headers, r.status_code)
                assert snapshot() == before, (method, path, headers)

    # Leer no se rechaza por origen (un enlace desde otra página es legítimo) porque leer nunca escribe.
    # Si algún día un GET modifica algo, esta comprobación falla y hay que protegerlo.
    for _, path, _ in routes:
        for method in ("GET", "HEAD"):
            client.request(method, path, headers={"Origin": f"https://{EVIL}", "Sec-Fetch-Site": "cross-site"})
            assert snapshot() == before, (method, path)

    # 3. Y la comparación no es vacía: las mismas solicitudes, desde la propia app, sí pasan y sí dejan rastro.
    #    (Lo que elimina va al final, para que lo demás tenga sobre qué actuar.)
    changed = set()
    for template, path, method in sorted(unsafe, key=lambda u: u[2] == "DELETE"):
        own = client.request(method, path, headers={"Origin": BASE_URL, "Sec-Fetch-Site": "same-origin"}, **bodies.get(template, {}))
        assert own.status_code not in (403, 421), (method, path)
        if own.status_code == 200 and (after := snapshot()) != before:
            changed.add((template, method))
            before = after
    assert changed >= {(t, "POST") for t in bodies if t != "/api/chats/{chat_id}"} | {
        ("/api/authors/{author_id}/unmerge", "POST"), ("/api/classify/runs/{run_id}/activate", "POST"),
        ("/api/chats/{chat_id}", "PATCH"), ("/api/chats/{chat_id}", "DELETE"),
        ("/api/newspaper/editions/{edition_id}/discard", "POST"), ("/api/newspaper/editions/{edition_id}/restore", "POST")}, changed


def test_the_frontend_only_talks_to_its_own_origin():
    """El frontend pide rutas relativas (el navegador les pone el Host y el Origin de la página): por eso validar
    ambos no le cambia nada. Si alguien escribe una dirección absoluta, esto falla antes que la app."""
    from test_api import FRONTEND
    for f in FRONTEND:
        code = re.sub(r"//.*", "", f.read_text(encoding="utf-8"))
        assert not re.search(r"""["'`]https?:""", code) and "127.0.0.1" not in code and "localhost" not in code, f.name
        for call in re.findall(r"fetch\(([^)]*)\)", code):
            assert call.startswith("path"), f"{f.name}: fetch({call})"
