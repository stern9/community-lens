"""Periódico: selección de enlaces, seudonimización, armado y contrato. Sin red y sin modelos reales:
se reemplaza solo quién abre las URLs (newspaper.NET) y quién envía a los proveedores (llm.TRANSPORT)."""
import json
import re

import pytest
from conftest import FakeLLM, FakeNet, api_client
from test_contract import check

from community_lens import llm, newspaper

CHAT = """[10/1/26, 9:00:00 AM] ~ Ana Demo: Miren esta nota https://example.org/notas/centros-de-datos
[10/1/26, 9:05:00 AM] +506 5555 0202: Llamen a Ana Demo al +506 5555 0909 o escriban a ana@example.org
[10/2/26, 8:00:00 AM] +506 5555 0202: https://example.net/analisis/carrera-ia?utm_source=x
[10/2/26, 8:10:00 AM] ~ Ana Demo: video https://youtu.be/abc123 y la herramienta https://tool.example.com/
[10/3/26, 7:00:00 AM] ~ Ana Demo: únanse https://chat.whatsapp.com/XXXX y lean https://caido.example.org/nota/rota
""".encode()

PAGE = "<html><head><title>{t}</title><meta property='og:site_name' content='Medio Demo'></head><body><nav>menú</nav><p>" + "Texto del artículo. " * 30 + "</p><script>alert(1)</script></body></html>"


def fake_site(url: str):
    """Lo que responde "internet" en estas pruebas: (estado, encabezados, cuerpo)."""
    if "caido" in url:
        return 404, {}, "no está"
    if "oembed" in url:
        return 200, {"Content-Type": "application/json"}, '{"title": "Video demo", "author_name": "Canal Demo"}'
    return 200, {"Content-Type": "text/html; charset=utf-8"}, PAGE.format(t="Título " + url.rsplit("/", 1)[-1])


def fake_json(provider, system, user):
    if system == newspaper.READING_SYSTEM:
        return {"parrafos": ["Primer párrafo.", "Segundo párrafo."], "temas": [{"tema": "Datos", "mensajes": [1, 2, 2, 999]}, {"tema": "Otro", "mensajes": [2, 3]}]}
    ids = {int(line.split()[2]): line for line in user.splitlines() if line.startswith("=== id ")}
    art = [i for i, l in ids.items() if "ARTÍCULO" in l]
    return {"hilo": "Un hilo", "principal": art[0],
            "articulos": [{"id": i, "titular": f"Titular {i} <b>", "medio": "Medio", "resumen": ["Una oración.", "https://inventado.example/x"]} for i in art]
                         + [{"id": 4242, "titular": "Inventado", "resumen": ["x"]}],
            "redes": [{"id": i, "cuenta": "Canal Demo", "texto": "Un video.", "video": True} for i, l in ids.items() if "RED SOCIAL" in l],
            "sitios": [{"id": i, "nombre": "Tool", "texto": "Un sitio."} for i, l in ids.items() if "SITIO" in l],
            "cronologia": [{"fecha": "1 oct", "texto": "Algo pasó.", "fuente": "Medio"}]}


def fake_model(provider, system, user):
    return json.dumps(fake_json(provider, system, user), ensure_ascii=False)


def approve_drafts(con) -> list[int]:
    """Aprueba los borradores pendientes, como haría quien administra el grupo entre una corrida y la siguiente."""
    from community_lens import edition
    ids = [r["id"] for r in con.execute("SELECT id FROM editions WHERE status='borrador' ORDER BY id")]
    for i in ids:
        edition.approve(con, i, "Prueba", "pytest", None)
    return ids


@pytest.fixture
def no_network(monkeypatch):
    """Internet y los proveedores de modelos, de mentira. Devuelve (red, transporte) para mirar qué se pidió."""
    net, models = FakeNet(fake_site), FakeLLM(fake_model)
    monkeypatch.setattr(newspaper, "NET", net)
    monkeypatch.setattr(llm, "TRANSPORT", models)
    return net, models


@pytest.fixture
def client(no_network, setenv):
    setenv(ANTHROPIC_API_KEY="k-demo")
    with api_client() as c:
        pv = c.post("/api/imports/preview", params={"filename": "chat.txt"}, content=CHAT).json()
        cid = c.post("/api/imports/commit", json={"token": pv["token"], "file_name": "chat.txt", "date_order": "MDY", "chat_name": "Demo"}).json()["chat_id"]
        yield c, cid


def test_link_kinds():
    k = newspaper.link_kind
    assert k("https://example.org/notas/x", "example.org") == "article"
    assert k("https://www.youtube.com/watch?v=1", "www.youtube.com") == "social"
    assert k("https://vt.tiktok.com/abc/", "vt.tiktok.com") == "social"
    assert k("https://tool.example.com/", "tool.example.com") == "tool"
    assert k("https://chat.whatsapp.com/abc", "chat.whatsapp.com") is None
    assert k("ftp://example.org/x", "example.org") is None


def test_same_link_with_and_without_tracking_counts_once(con):
    from conftest import do_import
    chat = b"""[10/1/26, 9:00:00 AM] ~ Ana Demo: https://example.org/notas/uno?utm_source=boletin
[10/2/26, 9:00:00 AM] ~ Beto Demo: https://www.example.org/notas/uno/
[10/2/26, 9:30:00 AM] ~ Beto Demo: https://example.org/notas/dos
[10/3/26, 9:00:00 AM] ~ Ana Demo: https://sitio.example/?utm_source=x&utm_medium=y
[10/3/26, 9:10:00 AM] ~ Beto Demo: https://sitio.example/
[10/3/26, 9:20:00 AM] ~ Ana Demo: https://example.org/buscar?utm_source=x&q=datos
"""
    cid = do_import(con, None, data=chat, file_name="chat.txt", date_order="MDY")["chat_id"]
    c = newspaper.candidates(con, cid, "2026-10-01", "2026-10-03", order="shared")
    assert c["total"] == {"article": 3, "social": 0, "tool": 1}
    # Un solo candidato con dos compartidas, primero en "más compartidos"; la dirección es una del chat, sin seguimiento.
    assert [(a["url"], a["shares"]) for a in c["items"]["article"]] == [
        ("https://example.org/notas/uno", 2), ("https://example.org/buscar?q=datos", 1), ("https://example.org/notas/dos", 1)]
    # La raíz de un sitio es una herramienta aunque se haya compartido con ?utm_…
    assert [(t["url"], t["shares"], t["kind"]) for t in c["items"]["tool"]] == [("https://sitio.example/", 2, "tool")]
    assert newspaper.link_kind("https://sitio.example/?utm_source=x", "sitio.example") == "tool"
    # La página se guarda una sola vez, bajo la identidad del enlace, y se reutiliza con cualquiera de sus formas.
    net = FakeNet(lambda url: (200, {}, PAGE.format(t="x")))
    art = c["items"]["article"][0]
    assert newspaper.load_pages(con, [art], net)[art["url"]]["status"] == "ok"
    other = {**art, "url": "https://www.example.org/notas/uno/?utm_medium=z"}
    assert newspaper.load_pages(con, [other], net)[other["url"]]["title"] == "x"
    assert [url for url, _ in net.requests] == ["https://example.org/notas/uno"]
    assert [r["url"] for r in con.execute("SELECT url FROM pages")] == ["https://example.org/notas/uno"]
    # Una base anterior guardó la página bajo la URL tal cual (con www y barra): también se reutiliza.
    con.execute("UPDATE pages SET url='https://www.example.org/notas/uno/'")
    assert newspaper.load_pages(con, [other | {"url": "https://www.example.org/notas/uno/"}], net) and len(net.requests) == 1


def test_preview_sends_nothing_and_matches_types(client, monkeypatch):
    c, cid = client
    monkeypatch.setattr(newspaper, "NET", FakeNet(lambda url: pytest.fail("la vista previa no debe descargar nada")))
    monkeypatch.setattr(llm, "TRANSPORT", FakeLLM(lambda *a: pytest.fail("la vista previa no debe llamar a un modelo")))
    p = c.get("/api/newspaper/preview", params={"chat_id": cid, "from": "2026-10-01", "to": "2026-10-03"}).json()
    check("NewspaperPreview", p)
    assert [len(p["links"][k]) for k in ("article", "social", "tool")] == [3, 1, 1] and p["links_skipped"] == 1
    assert p["facts"]["messages"] == 5 and p["facts"]["participants"] == 2
    assert p["providers"] == [{"name": "anthropic", "label": "Claude", "model": "claude-opus-5-5"}]
    assert "k-demo" not in str(p)


@pytest.mark.parametrize("status", ["ok", "failed"])
@pytest.mark.parametrize("canonical_exists", [False, True])
def test_old_page_cache_is_normalized_on_open(con, status, canonical_exists):
    """Abrir una base vieja deja una fila por identidad; reabrirla no vuelve a cambiar la caché."""
    from community_lens.config import settings
    from community_lens.db import connect
    url = "https://www.example.org:443/notas/uno/?utm_source=demo"
    key = "https://example.org/notas/uno"
    stamp = "2026-10-01T00:00:00Z"
    con.execute("INSERT INTO pages(url, kind, status, title, text, fetched_at) VALUES (?, 'article', ?, ?, ?, ?)",
                (url, status, "Anterior", "Texto sintético anterior", stamp))
    # Otra variante ya normalizada con la regla anterior, que todavía conservaba el puerto por defecto.
    con.execute("INSERT INTO pages(url, kind, status, title, text, fetched_at) VALUES (?, 'article', ?, ?, ?, ?)",
                ("https://example.org:443/notas/uno", status, "Anterior", "Texto sintético anterior", stamp))
    if canonical_exists:
        con.execute("INSERT INTO pages(url, kind, status, title, text, fetched_at) VALUES (?, 'article', 'ok', ?, ?, ?)",
                    (key, "Normalizada", "Texto sintético normalizado", stamp))
    expected_title = "Normalizada" if canonical_exists else "Anterior"
    net = FakeNet(fake_site)
    snapshot = None
    for _ in range(2):
        opened = connect(settings().db_path)
        try:
            rows = [dict(r) for r in opened.execute("SELECT * FROM pages")]
            assert len(rows) == 1 and rows[0]["url"] == key and rows[0]["title"] == expected_title
            assert rows[0]["fetched_at"] == stamp
            assert snapshot is None or snapshot == rows
            snapshot = rows
        finally:
            opened.close()
    pages = newspaper.load_pages(con, [{"url": url, "kind": "article"}], net)
    assert pages[url]["status"] == "ok" and pages[url]["url"] == url
    assert len(net.requests) == (1 if status == "failed" and not canonical_exists else 0)
    assert [r["url"] for r in con.execute("SELECT url FROM pages")] == [key]


@pytest.mark.parametrize("missing", ["from", "to", "both"])
def test_cleared_preview_dates_are_resolved_but_generation_requires_dates(client, missing):
    c, cid = client
    dates = {"from": "2026-10-01", "to": "2026-10-03"}
    for field in dates:
        if missing in (field, "both"):
            dates[field] = ""
    preview = c.get("/api/newspaper/preview", params={"chat_id": cid, **dates})
    assert preview.status_code == 200
    resolved = preview.json()
    assert resolved["from"] and resolved["to"] and resolved["from"] <= resolved["to"]
    assert c.post("/api/newspaper/editions", json={"chat_id": cid, **dates}).status_code == 422


def test_transcript_is_pseudonymized(con):
    from conftest import do_import
    cid = do_import(con, None, data=CHAT, file_name="chat.txt", date_order="MDY")["chat_id"]
    text = newspaper.transcript_text(newspaper.transcript(con, cid, "2026-10-01", "2026-10-03"))
    assert "Ana Demo" not in text and "5555" not in text and "ana@example.org" not in text
    assert "P01:" in text and "[participante]" in text and "[número]" in text and "[correo]" in text
    assert "https://example.org/notas/centros-de-datos" in text  # los enlaces no se tocan


def test_generate_uses_only_chat_links(client):
    c, cid = client
    body = {"chat_id": cid, "from": "2026-10-01", "to": "2026-10-03", "editor": "anthropic", "readers": [], "consent_chat": False,
            "order": "recent", "max_articles": 8}
    check("NewspaperRequest", body)
    r = c.post("/api/newspaper/editions", json=body)
    assert r.status_code == 200, r.text
    res = r.json()
    check("NewspaperResult", res)
    assert (res["articles"], res["socials"], res["tools"], res["unread"], res["readings"]) == (2, 1, 1, 1, 0)
    assert res["sent"] == {"pages_to": "Claude", "pages_tried": [], "pages": 4, "chat_messages": 0, "chat_to": []}
    page = c.get(f"/periodico/{res['id']}")
    assert "script-src" not in page.headers["content-security-policy"] and "default-src 'none'" in page.headers["content-security-policy"]
    html = page.text
    import re
    hrefs = set(re.findall(r'href="(https?://[^"]+)"', html))
    assert hrefs == {"https://example.org/notas/centros-de-datos", "https://example.net/analisis/carrera-ia", "https://youtu.be/abc123",
                     "https://tool.example.com/", "https://caido.example.org/nota/rota"}
    assert "Inventado" not in html and "<script" not in html and re.search(r"Titular \d &lt;b&gt;", html)   # ids inventados fuera; texto escapado
    assert "No se pudo leer" in html and "HTTP 404" in html and "La visión de" not in html
    assert "attachment" in c.get(f"/periodico/{res['id']}", params={"download": "true"}).headers["content-disposition"]
    check("NewspaperEdition", c.get("/api/newspaper/editions", params={"chat_id": cid}).json(), array=True)


def test_readings_need_explicit_consent_and_counts_come_from_code(client):
    c, cid = client
    body = {"chat_id": cid, "from": "2026-10-01", "to": "2026-10-03", "editor": "anthropic", "readers": ["anthropic"], "consent_chat": False}
    denied = c.post("/api/newspaper/editions", json=body)
    assert denied.status_code == 422 and "explícitamente" in denied.json()["detail"]
    check("ApiErrorBody", denied.json())
    res = c.post("/api/newspaper/editions", json={**body, "consent_chat": True}).json()
    assert res["readings"] == 1 and res["sent"]["chat_messages"] == 5 and res["sent"]["chat_to"] == ["Claude"]
    html = c.get(f"/periodico/{res['id']}").text
    assert "La visión de Claude" in html and "Primer párrafo." in html
    # Tema "Datos": mensajes 1 y 2 (el repetido y el inexistente no cuentan); "Otro": solo el 3, porque el 2 ya estaba asignado.
    assert "<span>Datos</span><b>2</b>" in html and "<span>Otro</span><b>1</b>" in html


def test_no_provider_configured(client, setenv):
    c, cid = client
    setenv(ANTHROPIC_API_KEY="", OPENAI_API_KEY="k2")  # key sin modelo: no se puede usar
    p = c.get("/api/newspaper/preview", params={"chat_id": cid, "from": "2026-10-01", "to": "2026-10-03"}).json()
    assert p["providers"] == [] and p["missing_model"] == ["OpenAI"]
    r = c.post("/api/newspaper/editions", json={"chat_id": cid, "from": "2026-10-01", "to": "2026-10-03", "editor": "openai"})
    assert r.status_code == 502 and "no está configurado" in r.json()["detail"]


def test_auto_uses_whatever_is_configured(client, no_network, setenv):
    c, cid = client
    setenv(OPENAI_API_KEY="k2", OPENAI_MODEL="modelo-demo", OPENAI_LABEL="Otro", GEMINI_API_KEY="", GEMINI_MODEL="sin-key")  # sin key: no cuenta

    def claude_down(provider, system, user):
        if provider == "anthropic" and system == newspaper.EDITOR_SYSTEM:
            return 529
        return fake_model(provider, system, user)
    models = no_network[1]
    models.answer = claude_down

    body = {"chat_id": cid, "from": "2026-10-01", "to": "2026-10-03", "editor": "auto", "readers": ["all"], "consent_chat": True}
    res = c.post("/api/newspaper/editions", json=body).json()
    check("NewspaperResult", res)
    # Redacta el siguiente configurado, queda dicho quién recibió qué, y cada modelo disponible da su lectura.
    assert res["sent"]["pages_to"] == "Otro" and res["sent"]["pages_tried"] == ["Claude"]
    assert res["sent"]["chat_to"] == ["Claude", "Otro"] and res["readings"] == 2
    assert any("Claude no pudo redactar (Anthropic respondió HTTP 529.)" in w for w in res["warnings"])
    # Cada proveedor recibió lo suyo con su propia key, y ninguna key aparece en lo que vuelve a la pantalla.
    sent = [(name, system == newspaper.EDITOR_SYSTEM) for name, _, _, body in models.requests
            for system in [body.get("system") or body["messages"][0]["content"]]]
    assert sent == [("anthropic", True), ("openai", True), ("anthropic", False), ("openai", False)]
    assert "k-demo" not in str(res) and "k2" not in str(res)
    html = c.get(f"/periodico/{res['id']}").text
    assert "La visión de Claude" in html and "La visión de Otro" in html and "con Otro (modelo-demo)" in html
    # Un modelo elegido por nombre no cae a otro, y "all" sigue exigiendo la confirmación.
    assert c.post("/api/newspaper/editions", json={**body, "editor": "anthropic", "readers": []}).status_code == 502
    assert c.post("/api/newspaper/editions", json={**body, "consent_chat": False}).status_code == 422


def _zip(name: str, data: bytes, extra: dict | None = None) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, data)
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()


def test_zip_export_is_imported_like_the_txt(client):
    c, _ = client
    z = _zip("_chat.txt", CHAT.replace(b"10/1/26", b"11/1/26").replace(b"10/2/26", b"11/2/26").replace(b"10/3/26", b"11/3/26"),
             {"00001-PHOTO.jpg": b"\xff\xd8 no es texto", "notas.txt": b"corto"})
    pv = c.post("/api/imports/preview", params={"filename": "WhatsApp Chat - Otro grupo.zip", "date_order": "MDY"}, content=z).json()
    check("ImportPreview", pv)
    assert pv["file_name"] == "WhatsApp Chat - Otro grupo.txt" and pv["diagnostics"]["participant_messages"] == 5
    bad = c.post("/api/imports/preview", params={"filename": "x.zip"}, content=_zip("foto.jpg", b"x"))
    assert bad.status_code == 422 and bad.json()["error"] == "zip_without_txt"


NOTICE = "[10/20/26, 9:00:00 AM] \u200eBeto Demo joined using this group's invite link\n".encode()


def test_default_range_is_the_same_on_screen_and_on_the_command_line(client, capsys):
    """El rango por defecto termina el último día con MENSAJES, aunque después haya avisos del sistema."""
    from community_lens import cli
    c, cid = client
    pv = c.post("/api/imports/preview", params={"filename": "chat.txt", "date_order": "MDY"}, content=CHAT + NOTICE).json()
    assert c.post("/api/imports/commit", json={"token": pv["token"], "file_name": "chat.txt", "date_order": "MDY", "chat_id": cid}).json()["status"] == "imported"
    assert c.get("/api/chats").json()[0]["last_record"].startswith("2026-10-20")
    p = c.get("/api/newspaper/preview", params={"chat_id": cid}).json()
    check("NewspaperPreview", p)
    assert (p["from"], p["to"], p["last_day"]) == ("2026-09-27", "2026-10-03", "2026-10-03")
    assert cli.periodico(["--grupo", "Demo"]) == 0
    assert f"({p['from']} a {p['to']})" in capsys.readouterr().out
    # Un solo extremo también se completa igual en los dos lados.
    assert c.get("/api/newspaper/preview", params={"chat_id": cid, "from": "2026-10-02"}).json()["to"] == "2026-10-03"
    assert cli.periodico(["--grupo-id", str(cid), "--desde", "2026-10-02"]) == 0
    assert "(2026-10-02 a 2026-10-03)" in capsys.readouterr().out
    assert cli.periodico(["--grupo-id", str(cid), "--hasta", "2026-10-02", "--dias", "2"]) == 0
    assert "(2026-10-01 a 2026-10-02)" in capsys.readouterr().out


def test_command_refuses_ambiguous_group_names(client, tmp_path, capsys):
    from community_lens import cli
    c, cid = client
    other = CHAT.replace(b"10/1/26", b"11/1/26").replace(b"10/2/26", b"11/2/26").replace(b"10/3/26", b"11/3/26")
    pv = c.post("/api/imports/preview", params={"filename": "otro.txt", "date_order": "MDY"}, content=other).json()
    twin = c.post("/api/imports/commit", json={"token": pv["token"], "file_name": "otro.txt", "date_order": "MDY", "chat_name": "Demo"}).json()["chat_id"]
    assert twin != cid
    export = tmp_path / "nuevo.txt"
    export.write_bytes(CHAT.replace(b"10/", b"12/"))
    for args in (["--grupo", "Demo"], ["--grupo", "Demo", "--solo-publicar", "--salida", str(tmp_path / "sitio")],
                 ["--grupo", "Demo", "--archivo", str(export), "--orden-fecha", "MDY"]):
        assert cli.periodico(args) == 1
        err = capsys.readouterr().err
        assert "Hay 2 grupos llamados «Demo»" in err and f"id {cid} " in err and f"id {twin} " in err and "Traceback" not in err
    assert not (tmp_path / "sitio").exists() and len(c.get("/api/chats").json()) == 2          # no publicó ni importó nada
    assert c.get("/api/newspaper/editions", params={"chat_id": cid}).json() == []
    # Con el id no hay duda.
    assert cli.periodico(["--grupo-id", str(twin), "--dias", "3"]) == 0
    assert "(2026-11-01 a 2026-11-03)" in capsys.readouterr().out
    assert len(c.get("/api/newspaper/editions", params={"chat_id": twin}).json()) == 1
    for args, why in ((["--grupo-id", "999"], "No hay ningún grupo con id 999"), (["--grupo", "No existe"], "Indicá --archivo"),
                      (["--grupo", "No existe", "--archivo", str(tmp_path / "falta.zip")], "No existe el archivo")):
        assert cli.periodico(args) == 1 and why in capsys.readouterr().err


def test_command_validates_like_the_screen(client, capsys):
    from community_lens import cli
    c, cid = client
    for args, why in ((["--desde", "2026-10-99", "--hasta", "2026-10-03"], "Fecha inválida: «2026-10-99»"),
                      (["--desde", "1/10/2026", "--hasta", "2026-10-03"], "Fecha inválida"),
                      (["--hasta", "ayer"], "Fecha inválida: «ayer»"),
                      (["--desde", "2026-10-03", "--hasta", "2026-10-01"], "invertido"),
                      (["--dias", "0"], "al menos un día"),
                      (["--articulos", "50"], "El máximo de artículos va de 3 a 15"),
                      (["--articulos", "1"], "El máximo de artículos va de 3 a 15")):
        assert cli.periodico(["--grupo", "Demo", *args]) == 1, args
        err = capsys.readouterr().err
        assert why in err and err.startswith("No se pudo:") and "Traceback" not in err, args
    assert c.get("/api/newspaper/editions", params={"chat_id": cid}).json() == []
    # La pantalla rechaza lo mismo, con el mismo mensaje.
    r = c.get("/api/newspaper/preview", params={"chat_id": cid, "from": "2026-10-99", "to": "2026-10-03"})
    assert r.status_code == 422 and "Fecha inválida: «2026-10-99»" in r.json()["detail"]
    r = c.post("/api/newspaper/editions", json={"chat_id": cid, "from": "2026-10-01", "to": "2026-10-03", "editor": "anthropic", "max_articles": 50})
    assert r.status_code == 422 and "El máximo de artículos va de 3 a 15" in r.json()["detail"]


def test_headless_command_imports_generates_and_publishes(tmp_path, no_network, capsys, setenv):
    from community_lens import cli
    setenv(ANTHROPIC_API_KEY="k-demo")
    export = tmp_path / "Demo.zip"
    export.write_bytes(_zip("_chat.txt", CHAT))
    site = tmp_path / "sitio"
    args = ["--archivo", str(export), "--grupo", "Demo", "--orden-fecha", "MDY", "--dias", "3", "--salida", str(site)]
    assert cli.periodico(args) == 0
    out = capsys.readouterr().out
    assert "2026-10-01 a 2026-10-03" in out and "nada del chat" in out
    # La edición recién generada es un borrador: el sitio se escribe, pero sin ella.
    assert (site / "index.html").exists() and not (site / "ediciones" / "2026-10-01_a_2026-10-03.html").exists()
    from community_lens.db import connect
    con = connect()
    assert len(approve_drafts(con)) == 1
    con.close()
    # Correrlo otra vez con el mismo archivo no duplica mensajes y deja una sola página por rango: la aprobada.
    assert cli.periodico(args) == 0
    assert "ya estaba importado" in capsys.readouterr().out
    assert (site / "ediciones" / "2026-10-01_a_2026-10-03.html").exists()
    index = (site / "index.html").read_text()
    assert 'href="ediciones/2026-10-01_a_2026-10-03.html"' in index and "<script" not in index
    assert len(list((site / "ediciones").glob("*.html"))) == 1
    # Las lecturas sin la confirmación explícita se rechazan antes de enviar nada.
    assert cli.periodico(["--grupo", "Demo", "--dias", "3", "--lecturas", "anthropic"]) == 1
    assert "explícitamente" in capsys.readouterr().err


# ------------------------------------------------------- ediciones guardadas --

def stored_edition(con, chat_id, data, sent, html="<html>página guardada por una versión anterior</html>"):
    """Inserta una edición como la dejó una versión anterior de la app (con su columna html llena)."""
    return con.execute("""INSERT INTO editions(chat_id, date_from, date_to, created_at, editor, sent, data, html)
                          VALUES (?,?,?,?,?,?,?,?)""",
                       (chat_id, data["from"], data["to"], "2026-10-10T18:00:00Z", json.dumps(data["editor"]), json.dumps(sent),
                        json.dumps(data, ensure_ascii=False), html)).lastrowid


def test_edition_saved_with_the_previous_format_still_opens_and_publishes(client, tmp_path):
    """tests/fixtures/edition_v1.json es una edición tal como la guardaba la app antes de que el formato tuviera versión."""
    from conftest import FIX

    from community_lens import edition
    from community_lens.db import connect
    c, cid = client
    v1 = json.loads((FIX / "edition_v1.json").read_text(encoding="utf-8"))
    assert "format" not in v1["data"]
    # Una todavía más vieja: sin los campos que se fueron agregando.
    older = json.loads(json.dumps(v1["data"])) | {"from": "2026-09-01", "to": "2026-09-07"}
    for key in ("thread", "range_label", "readings"):
        del older[key]
    for key in ("kicker", "note", "byline", "published"):
        del older["lead"][key]
    for s in older["socials"]:
        del s["account"], s["video"], s["preview"]
    for t in older["tools"]:
        del t["preview"]
    old_sent = {k: v for k, v in v1["sent"].items() if k != "pages_tried"}
    con = connect()
    a, b = stored_edition(con, cid, v1["data"], v1["sent"]), stored_edition(con, cid, older, old_sent)

    page = c.get(f"/periodico/{a}")
    assert page.status_code == 200 and "página guardada" not in page.text          # se dibuja desde los datos, no desde html
    for text in ("Lo que se compartió", "Demo", "1 al 3 de octubre", "Sábado 10 de octubre de 2026", "Un hilo", "Titular 2 &lt;b&gt;",
                 "La visión de Claude", "<span>Datos</span><b>2</b>", "Canal Demo", "Compartido 2 oct", "No se pudo leer", "HTTP 404",
                 "5 mensajes de 2 participantes", "con Claude (claude-opus-5-5)"):
        assert text in page.text, text
    assert set(re.findall(r'href="(https?://[^"]+)"', page.text)) == {
        "https://example.org/notas/centros-de-datos", "https://example.net/analisis/carrera-ia", "https://youtu.be/abc123",
        "https://tool.example.com/", "https://caido.example.org/nota/rota"}
    old_page = c.get(f"/periodico/{b}")
    assert old_page.status_code == 200 and "1 al 7 de septiembre" in old_page.text and "Hilo de la edición" not in old_page.text
    assert "La visión de" not in old_page.text and "Cuenta no disponible" not in old_page.text and "sin vista previa" not in old_page.text

    listed = c.get("/api/newspaper/editions", params={"chat_id": cid}).json()
    check("NewspaperEdition", listed, array=True)
    assert [e["id"] for e in listed] == [b, a] and listed[0]["sent"]["pages_tried"] == []
    site = newspaper.publish(con, cid, tmp_path / "sitio")
    files = site["files"]
    assert site["skipped"] == [] and files == ["index.html", "ediciones/2026-10-01_a_2026-10-03.html", "ediciones/2026-09-01_a_2026-09-07.html"]
    # Heredada con lecturas: se publica dibujada en versión pública, la misma que ?publico=1 y sin las lecturas.
    public = c.get(f"/periodico/{a}", params={"publico": 1}).text
    assert (tmp_path / "sitio" / files[1]).read_text(encoding="utf-8") == public != page.text
    assert "La visión de" not in public and "En números" not in public and "Demo ·" not in public and "Titular 2 &lt;b&gt;" in public
    index = (tmp_path / "sitio" / "index.html").read_text(encoding="utf-8")
    assert "Del 1 al 3 de octubre de 2026 · 2 artículos" in index and "Del 1 al 7 de septiembre de 2026" in index

    # Abrir no reescribe nada: lo guardado queda como estaba.
    rows = con.execute("SELECT data, html FROM editions ORDER BY id").fetchall()
    assert json.loads(rows[0]["data"]) == v1["data"] and json.loads(rows[1]["data"]) == older and "página guardada" in rows[0]["html"]
    # upgrade() es la única que conoce las formas viejas: deja todos los campos, y es idempotente.
    up = edition.upgrade(older)
    assert up["format"] == edition.FORMAT and set(up) == set(edition.Edition.__annotations__) and edition.upgrade(up) is up
    assert set(up["lead"]) == set(edition.Article.__annotations__) and set(up["socials"][0]) == set(edition.Social.__annotations__)
    with pytest.raises(ValueError, match="formato"):
        edition.upgrade({**up, "format": edition.FORMAT + 1})
    future = stored_edition(con, cid, {**up, "format": edition.FORMAT + 1}, v1["sent"])
    assert c.get(f"/periodico/{future}").status_code == 422 and c.get("/periodico/99999").status_code == 404
    con.close()


def test_new_editions_declare_their_format_and_do_not_store_the_page(client):
    from community_lens import edition
    from community_lens.db import connect
    c, cid = client
    res = c.post("/api/newspaper/editions", json={"chat_id": cid, "from": "2026-10-01", "to": "2026-10-03", "editor": "anthropic",
                                                   "readers": ["anthropic"], "consent_chat": True}).json()
    con = connect()
    row = con.execute("SELECT data, html FROM editions WHERE id=?", (res["id"],)).fetchone()
    con.close()
    data = json.loads(row["data"])
    assert row["html"] == "" and data["format"] == edition.FORMAT and set(data) == set(edition.Edition.__annotations__)
    assert set(data["lead"]) == set(edition.Article.__annotations__) and set(data["readings"][0]) == set(edition.Reading.__annotations__)
    assert set(data["socials"][0]) == set(edition.Social.__annotations__) and set(data["tools"][0]) == set(edition.Tool.__annotations__)
    assert set(data["unread"][0]) == set(edition.Unread.__annotations__) and set(data["timeline"][0]) == set(edition.TimelineEntry.__annotations__)
    assert set(res["sent"]) == set(edition.Sent.__annotations__) and set(data["facts"]) == set(edition.Facts.__annotations__)
    # Lo único nuevo respecto de la forma anterior es el número de formato.
    v1 = json.loads((__import__("conftest").FIX / "edition_v1.json").read_text(encoding="utf-8"))["data"]
    assert {k: v for k, v in data.items() if k not in ("format", "created_label")} == {k: v for k, v in v1.items() if k != "created_label"}


# ------------------------------------------------------- --continuar (#29) --
# Exportaciones acumulativas, como las de WhatsApp: cada una trae el chat desde el principio hasta el día en
# que se exporta. Cada día tiene un artículo de mañana y otro de noche, así el último día queda a medias.

def _export(last: str, first: str = "2026-10-01", per_day: int = 2) -> bytes:
    from datetime import date, timedelta
    d, end, lines = date.fromisoformat(first), date.fromisoformat(last), []
    while d <= end:
        for hour in ("9:00:00 AM", "8:00:00 PM")[:per_day]:
            lines.append(f"[{d.month}/{d.day}/{d:%y}, {hour}] ~ Ana Demo: lean https://example.org/notas/{d}-{hour[0]}")
        d += timedelta(days=1)
    return ("\n".join(lines) + "\n").encode()


def _covered(con, cid) -> list[str]:
    """Todos los días cubiertos por las ediciones guardadas, con repeticiones si las hubiera."""
    from datetime import date, timedelta
    days = []
    for e in newspaper.editions(con, cid):
        d = date.fromisoformat(e["from"])
        while d <= date.fromisoformat(e["to"]):
            days.append(d.isoformat())
            d += timedelta(days=1)
    return sorted(days)


APPROVE_NOW = ("--aprobar", "--aprobado-por", "Prueba", "--canal", "pytest")  # aprobar al vuelo: solo en pruebas o a mano


def _run(tmp_path, last: str, *extra: str, **kw) -> int:
    from community_lens import cli
    f = tmp_path / f"export_{last}.txt"
    f.write_bytes(_export(last, **kw))
    return cli.periodico(["--archivo", str(f), "--grupo", "Semanal", "--orden-fecha", "MDY", "--continuar", *extra])


def test_continue_range_follows_the_last_edition(con):
    from conftest import do_import

    from community_lens import edition
    cid = do_import(con, "c.txt", data=_export("2026-10-20"), date_order="MDY")["chat_id"]
    # Sin ediciones: 7 días que terminan el día anterior al último con mensajes (ese está a medias).
    assert newspaper.continue_range(con, cid) == ("2026-10-13", "2026-10-19")
    edition.save(con, cid, {"from": "2026-10-01", "to": "2026-10-09", "editor": {}}, {})
    edition.save(con, cid, {"from": "2026-10-03", "to": "2026-10-05", "editor": {}}, {})
    # Un borrador no cuenta: todavía no se publicó.
    assert newspaper.continue_range(con, cid) == ("2026-10-13", "2026-10-19")
    con.execute("UPDATE editions SET status='aprobada'")  # cuenta la que llega más lejos
    assert newspaper.continue_range(con, cid) == ("2026-10-10", "2026-10-19")
    last = edition.save(con, cid, {"from": "2026-10-10", "to": "2026-10-19", "editor": {}}, {})
    con.execute("UPDATE editions SET status='aprobada' WHERE id=?", (last,))
    with pytest.raises(newspaper.NoNews, match="Sin novedad: la última edición ya cubre hasta el 2026-10-19"):
        newspaper.continue_range(con, cid)
    # Una descartada reabre su período; una heredada (anterior a la aprobación) cuenta como publicada.
    edition.discard(con, last)
    assert newspaper.continue_range(con, cid) == ("2026-10-10", "2026-10-19")
    con.execute("UPDATE editions SET status='heredada' WHERE id=?", (last,))
    with pytest.raises(newspaper.NoNews):
        newspaper.continue_range(con, cid)


def test_continue_leaves_no_gaps_or_repeats(no_network, setenv, tmp_path, con, capsys):
    setenv(ANTHROPIC_API_KEY="k-demo")
    # Una semana, otra, se salta una, y dos exportaciones seguidas. Cada corrida aprueba al vuelo lo que genera.
    for last in ("2026-10-08", "2026-10-15", "2026-10-29"):
        assert _run(tmp_path, last, *APPROVE_NOW) == 0
    out = capsys.readouterr().out
    assert "(2026-10-01 a 2026-10-07)" in out and "(2026-10-08 a 2026-10-14)" in out and "(2026-10-15 a 2026-10-28)" in out
    # Al día siguiente solo hay un día completo nuevo con 2 artículos: no alcanza y queda para la próxima.
    assert _run(tmp_path, "2026-10-30") == 3
    assert "hacen falta 3" in capsys.readouterr().out
    assert _run(tmp_path, "2026-10-31", *APPROVE_NOW) == 0
    assert "(2026-10-29 a 2026-10-30)" in capsys.readouterr().out
    cid = newspaper.find_chat(con, "Semanal")["id"]
    days = _covered(con, cid)
    assert len(days) == len(set(days)) == 30 and (days[0], days[-1]) == ("2026-10-01", "2026-10-30")


def test_same_export_twice_does_not_call_the_model_again(no_network, setenv, tmp_path, con, capsys):
    setenv(ANTHROPIC_API_KEY="k-demo")
    net, models = no_network
    assert _run(tmp_path, "2026-10-08") == 0
    approve_drafts(con)
    calls, pages = len(models.requests), len(net.requests)
    assert calls > 0 and pages > 0
    capsys.readouterr()
    assert _run(tmp_path, "2026-10-08") == 3
    io = capsys.readouterr()
    assert "ya estaba importado" in io.out and "Aviso: Sin novedad: la última edición ya cubre hasta el 2026-10-07" in io.out and not io.err
    # La promesa es doble: ni el modelo ni las páginas; no sale nada de la máquina.
    assert len(models.requests) == calls and len(net.requests) == pages
    assert len(newspaper.editions(con, newspaper.find_chat(con, "Semanal")["id"])) == 1


def test_continue_needs_the_minimum_of_articles(no_network, setenv, tmp_path, con, capsys):
    setenv(ANTHROPIC_API_KEY="k-demo")
    net, models = no_network
    assert _run(tmp_path, "2026-10-02") == 3  # el único día completo trae 2 artículos
    assert "Aviso: El rango trae 2 artículo(s) y hacen falta 3" in capsys.readouterr().out
    # Ni el modelo ni las páginas: no sale nada de la máquina, y no se guarda nada.
    assert models.requests == net.requests == [] and newspaper.editions(con, newspaper.find_chat(con, "Semanal")["id"]) == []


def test_articles_over_the_cap_are_reported(no_network, setenv, con):
    from conftest import do_import
    setenv(ANTHROPIC_API_KEY="k-demo")
    cid = do_import(con, "c.txt", data=_export("2026-10-07"), date_order="MDY")["chat_id"]
    res = newspaper.generate(con, cid, "Demo", "2026-10-01", "2026-10-07", editor="auto", readers=[], consent_chat=False)
    assert "6 de los 14 artículos del rango quedaron fuera por el tope de 8." in res["warnings"]
    res = newspaper.generate(con, cid, "Demo", "2026-10-01", "2026-10-04", editor="auto", readers=[], consent_chat=False)
    assert not any("quedaron fuera" in w for w in res["warnings"])


def test_continue_refuses_an_explicit_range(client, capsys):
    from community_lens import cli
    for extra in (["--desde", "2026-10-01"], ["--hasta", "2026-10-02"], ["--dias", "7"]):
        with pytest.raises(SystemExit) as exit_:
            cli.periodico(["--grupo", "Demo", "--continuar", *extra])
        assert exit_.value.code == 2 and "no se combina con --dias, --desde ni --hasta" in capsys.readouterr().err


# ---------------------------------- códigos de salida, --carpeta y --json (#32, #10) --

def _folder(tmp_path, *lasts: str, name: str = "exportaciones"):
    """Carpeta con una exportación por fecha de corte; la fecha de modificación sigue el orden en que se pasan."""
    import os
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    for n, last in enumerate(lasts):
        f = d / f"WhatsApp Chat {last}.txt"
        f.write_bytes(_export(last))
        os.utime(f, (1_800_000_000 + n * 3600,) * 2)
    return d


def test_nothing_to_publish_exits_3_and_leaves_the_site_alone(no_network, setenv, tmp_path, con, capsys):
    setenv(ANTHROPIC_API_KEY="k-demo")
    site = tmp_path / "sitio"
    assert _run(tmp_path, "2026-10-02", "--salida", str(site)) == 3  # material insuficiente
    io = capsys.readouterr()
    assert "Aviso: El rango trae 2 artículo(s)" in io.out and "No se pudo" not in io.err + io.out
    assert not (site / "index.html").exists()
    assert _run(tmp_path, "2026-10-08") == 0
    approve_drafts(con)
    capsys.readouterr()
    assert _run(tmp_path, "2026-10-08", "--salida", str(site)) == 3  # sin novedad
    io = capsys.readouterr()
    assert "Aviso: Sin novedad" in io.out and "No se pudo" not in io.err and not (site / "index.html").exists()
    # Para reescribir el sitio igual está --solo-publicar, que termina en 0.
    from community_lens import cli
    assert cli.periodico(["--grupo", "Semanal", "--solo-publicar", "--salida", str(site)]) == 0
    assert (site / "index.html").exists()


def test_errors_exit_1(no_network, setenv, tmp_path, con, capsys):
    from community_lens import cli
    setenv(ANTHROPIC_API_KEY="")
    assert _run(tmp_path, "2026-10-08") == 1
    assert capsys.readouterr().err.startswith("No se pudo: No hay ningún modelo configurado")
    assert cli.periodico(["--archivo", str(tmp_path / "falta.zip"), "--grupo", "Semanal"]) == 1
    assert "No se pudo: No existe el archivo" in capsys.readouterr().err


def test_folder_takes_the_newest_export(no_network, setenv, tmp_path, con, capsys):
    import os

    from community_lens import cli
    setenv(ANTHROPIC_API_KEY="k-demo")
    d = _folder(tmp_path, "2026-10-08", "2026-10-15")
    for junk in (".DS_Store", "informe.pdf"):  # más nuevos, pero no son exportaciones
        (d / junk).write_bytes(b"x")
        os.utime(d / junk, (1_900_000_000,) * 2)
    (d / "sub").mkdir()
    before = sorted((p.name, p.stat().st_mtime) for p in d.iterdir())
    assert cli.periodico(["--carpeta", str(d), "--grupo", "Semanal", "--orden-fecha", "MDY", "--continuar"]) == 0
    out = capsys.readouterr().out
    assert "Exportación más nueva de la carpeta: WhatsApp Chat 2026-10-15.txt\n" in out and str(d) not in out
    assert "(2026-10-08 a 2026-10-14)" in out
    assert sorted((p.name, p.stat().st_mtime) for p in d.iterdir()) == before  # no borra, mueve ni toca nada


def test_folder_twice_with_continue_is_idempotent(no_network, setenv, tmp_path, con, capsys):
    from community_lens import cli
    setenv(ANTHROPIC_API_KEY="k-demo")
    models = no_network[1]
    args = ["--carpeta", str(_folder(tmp_path, "2026-10-08")), "--grupo", "Semanal", "--orden-fecha", "MDY", "--continuar"]
    assert cli.periodico(args) == 0
    approve_drafts(con)
    calls = len(models.requests)
    cid = newspaper.find_chat(con, "Semanal")["id"]
    capsys.readouterr()
    assert cli.periodico([*args[:2], "--grupo-id", str(cid), *args[4:]]) == 3
    assert "ya estaba importado" in capsys.readouterr().out and len(models.requests) == calls
    assert len(newspaper.editions(con, cid)) == 1


def test_folder_rules(no_network, setenv, tmp_path, con, capsys):
    from community_lens import cli
    setenv(ANTHROPIC_API_KEY="k-demo")
    empty = tmp_path / "vacia"
    empty.mkdir()
    (empty / ".DS_Store").write_bytes(b"x")
    (empty / "otro.pdf").write_bytes(b"x")
    for folder, why in ((empty, "no tiene ninguna exportación .zip o .txt"), (tmp_path / "no-existe", "No existe la carpeta")):
        assert cli.periodico(["--carpeta", str(folder), "--grupo", "Semanal"]) == 1
        assert why in capsys.readouterr().err
    d = _folder(tmp_path, "2026-10-08")
    for args, why in ((["--carpeta", str(d)], "--carpeta exige --grupo-id o --grupo"),
                      (["--carpeta", str(d), "--archivo", str(d / "x.txt"), "--grupo", "Semanal"], "not allowed with argument")):
        with pytest.raises(SystemExit) as exit_:
            cli.periodico(args)
        assert exit_.value.code == 2 and why in capsys.readouterr().err
    assert con.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 0  # no importó nada


def test_json_output(no_network, setenv, tmp_path, con, capsys):
    from community_lens import cli
    setenv(ANTHROPIC_API_KEY="k-demo")
    site = tmp_path / "sitio"

    def run(*args: str) -> tuple[int, dict, str]:
        code = cli.periodico([*args, "--json"])
        io = capsys.readouterr()
        return code, json.loads(io.out), io.err

    code, res, err = run("--archivo", str(_folder(tmp_path, "2026-10-02") / "WhatsApp Chat 2026-10-02.txt"), "--grupo", "Semanal",
                         "--orden-fecha", "MDY", "--continuar", "--salida", str(site))
    assert (code, res["status"], res["edition"], res["site"]) == (3, "material_insuficiente", None, None) and "hacen falta 3" in res["detail"]
    assert res["import"] == {"file": "WhatsApp Chat 2026-10-02.txt", "status": "imported", "chat_id": res["import"]["chat_id"], "added": 4, "matched_existing": 0}
    assert "Importado en el grupo" in err and "Aviso:" in err
    cid = res["import"]["chat_id"]
    d = _folder(tmp_path, "2026-10-08", name="otra")
    code, res, err = run("--carpeta", str(d), "--grupo-id", str(cid), "--orden-fecha", "MDY", "--continuar", "--salida", str(site))
    assert (code, res["status"], res["detail"]) == (0, "generada", None)
    assert res["import"]["status"] == "imported" and res["import"]["matched_existing"] == 4 and res["import"]["added"] == 12
    ed = res["edition"]
    assert (ed["from"], ed["to"], ed["readings"]) == ("2026-10-01", "2026-10-07", 0) and ed["articles"] > 0
    assert ed["sent"]["pages_to"] == "Claude" and ed["sent"]["chat_messages"] == 0 and ed["sent"] == newspaper.editions(con, cid)[0]["sent"]
    assert res["site"] == {"dir": str(site), "files": 1, "skipped": []} and isinstance(res["warnings"], list)  # el borrador no entra al sitio
    assert (ed["status"], ed["approval"], ed["in_site"]) == ("borrador", None, False) and "quedó como borrador" in err
    assert "Exportación más nueva de la carpeta" in err and "Edición" in err and "Sitio escrito" in err
    # Ninguna clave trae texto del chat: ni enlaces ni nombres.
    out = json.dumps(res)
    assert "example.org" not in out and "Ana" not in out and "lean" not in out
    assert approve_drafts(con) == [ed["id"]]
    code, res, _ = run("--carpeta", str(d), "--grupo-id", str(cid), "--orden-fecha", "MDY", "--continuar")
    assert (code, res["status"], res["edition"]) == (3, "sin_novedad", None) and res["import"]["status"] == "duplicate"
    assert res["detail"].startswith("Sin novedad")
    code, res, err = run("--grupo-id", str(cid), "--solo-publicar", "--salida", str(site))
    assert (code, res["status"], res["import"], res["edition"], res["site"]["files"]) == (0, "publicada", None, None, 2)
    code, res, err = run("--grupo-id", "999")
    assert (code, res["status"]) == (1, "error") and "No hay ningún grupo con id 999" in res["detail"] and "No se pudo:" in err
    # Sin --json, la salida estándar sigue siendo solo para personas.
    assert cli.periodico(["--grupo-id", str(cid), "--solo-publicar", "--salida", str(site)]) == 0
    assert "{" not in capsys.readouterr().out


def test_site_folder_that_is_a_file_fails_cleanly_and_says_the_edition_was_saved(no_network, setenv, tmp_path, con, capsys):
    """Un error del sistema de archivos (o de SQLite) no da traceback: código 1, JSON con status "error" y, como la
    edición ya se guardó, el mensaje dice cómo publicarla."""
    setenv(ANTHROPIC_API_KEY="k-demo")
    not_a_dir = tmp_path / "sitio"
    not_a_dir.write_text("ocupado")
    assert _run(tmp_path, "2026-10-08", "--salida", str(not_a_dir), "--json") == 1
    io = capsys.readouterr()
    out = json.loads(io.out)
    assert out["status"] == "error" and out["edition"]["id"] and out["site"] is None
    eid = out["edition"]["id"]
    assert f"quedó guardada como borrador: para aprobarla y publicarla corré --edicion {eid} --aprobar" in out["detail"] and "No se pudo:" in io.err
    assert len(newspaper.editions(con, newspaper.find_chat(con, "Semanal")["id"])) == 1


def test_solo_publicar_needs_salida_and_excludes_continuar(client, capsys):
    from community_lens import cli
    for args in (["--grupo", "Demo", "--solo-publicar"], ["--grupo", "Demo", "--solo-publicar", "--salida", "x", "--continuar"]):
        with pytest.raises(SystemExit) as exit_:
            cli.periodico(args)
        assert exit_.value.code == 2 and "necesita --salida y no se combina con --continuar" in capsys.readouterr().err


# ------------------------------------------------- borrador, aprobación y publicación (#7) --

APPROVE = {"by": "Jaime", "via": "WhatsApp"}
BAND = '<p class="fp-draft">Borrador, no compartir</p>'  # la regla .fp-draft de la hoja va en toda página; la banda, no


def _generate(c, cid, d_from="2026-10-01", d_to="2026-10-03") -> int:
    r = c.post("/api/newspaper/editions", json={"chat_id": cid, "from": d_from, "to": d_to, "editor": "anthropic"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _row(c, cid, eid) -> dict:
    """La edición tal como la muestra la lista."""
    rows = c.get("/api/newspaper/editions", params={"chat_id": cid}).json()
    check("NewspaperEdition", rows, array=True)
    return next(r for r in rows if r["id"] == eid)


def _filename(resp) -> str:
    return re.search(r'filename="([^"]+)"', resp.headers["content-disposition"])[1]


# El CREATE TABLE de editions antes de #7: sin status, approval ni approved_html.
OLD_EDITIONS = """CREATE TABLE editions (
    id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE, date_from TEXT NOT NULL,
    date_to TEXT NOT NULL, created_at TEXT NOT NULL, editor TEXT NOT NULL, sent TEXT NOT NULL, data TEXT NOT NULL, html TEXT NOT NULL)"""


@pytest.mark.parametrize("cut", [False, True], ids=["vieja", "migracion_cortada"])
def test_old_database_gains_the_columns_and_its_editions_stay_published(tmp_path, no_network, cut):
    """Una base de antes de la aprobación: sus ediciones quedan "heredada", se siguen publicando como siempre, sin
    banda, y cuentan para --continuar. Con cut, un proceso anterior alcanzó a agregar solo la primera columna."""
    import sqlite3

    from conftest import FIX, do_import

    from community_lens import db, edition
    from community_lens.config import settings
    raw = sqlite3.connect(settings().db_path)
    raw.execute(OLD_EDITIONS)
    if cut:
        raw.execute("ALTER TABLE editions ADD COLUMN status TEXT NOT NULL DEFAULT 'heredada'")
    raw.executescript(db.SCHEMA)  # el IF NOT EXISTS saltea editions y crea todo lo demás
    raw.execute("INSERT INTO chats(id, name, timezone, created_at) VALUES (1, 'Demo', 'America/Costa_Rica', '2026-10-01T00:00:00Z')")
    v1 = json.loads((FIX / "edition_v1.json").read_text(encoding="utf-8"))
    data = edition.upgrade(v1["data"])
    eid = raw.execute("INSERT INTO editions(chat_id, date_from, date_to, created_at, editor, sent, data, html) VALUES (1,?,?,?,?,?,?,'')",
                      (data["from"], data["to"], "2026-10-10T18:00:00Z", json.dumps(data["editor"]), json.dumps(v1["sent"]),
                       json.dumps(data, ensure_ascii=False))).lastrowid
    raw.commit()
    raw.close()

    con = db.connect()
    assert {"status", "approval", "approved_html"} <= {r["name"] for r in con.execute("PRAGMA table_info(editions)")}
    assert tuple(con.execute("SELECT status, approval, approved_html FROM editions").fetchone()) == ("heredada", None, None)
    site = newspaper.publish(con, 1, tmp_path / "sitio")
    assert site == {"files": ["index.html", "ediciones/2026-10-01_a_2026-10-03.html"], "skipped": []}
    published = (tmp_path / "sitio" / site["files"][1]).read_text(encoding="utf-8")
    with api_client() as c:
        page = c.get(f"/periodico/{eid}")
        assert page.status_code == 200 and 'class="fp-draft"' not in page.text and "BORRADOR" not in page.text
        assert c.get(f"/periodico/{eid}", params={"publico": 1}).text == published  # el sitio lleva la versión pública
        assert _filename(c.get(f"/periodico/{eid}", params={"download": "true"})) == "periodico_2026-10-01_a_2026-10-03.html"
        row = _row(c, 1, eid)
        assert {k: row[k] for k in ("status", "approval", "hash_ok", "up_to_date", "in_site")} == {
            "status": "heredada", "approval": None, "hash_ok": None, "up_to_date": None, "in_site": True}
    do_import(con, None, data=CHAT, file_name="chat.txt", date_order="MDY", chat_id=1)
    with pytest.raises(newspaper.NoNews):  # la heredada cubre hasta el 3 de octubre, el último día completo
        newspaper.continue_range(con, 1)
    # Reabrir no cambia nada.
    before = [tuple(r) for r in con.execute("SELECT * FROM editions")]
    con.close()
    con = db.connect()
    assert [tuple(r) for r in con.execute("SELECT * FROM editions")] == before
    con.close()


def test_a_draft_is_not_published_and_its_page_says_so(client, tmp_path):
    from community_lens import cli
    from community_lens.db import connect
    c, cid = client
    res = c.post("/api/newspaper/editions", json={"chat_id": cid, "from": "2026-10-01", "to": "2026-10-03", "editor": "anthropic"}).json()
    check("NewspaperResult", res)
    eid = res["id"]
    assert res["status"] == "borrador" and _row(c, cid, eid)["status"] == "borrador" and not _row(c, cid, eid)["in_site"]
    con = connect()
    site = newspaper.publish(con, cid, tmp_path / "sitio")
    assert site == {"files": ["index.html"], "skipped": []} and "Todavía no hay ediciones" in (tmp_path / "sitio" / "index.html").read_text()
    assert cli.periodico(["--grupo-id", str(cid), "--solo-publicar", "--salida", str(tmp_path / "otro")]) == 0
    assert list((tmp_path / "otro" / "ediciones").iterdir()) == []
    # Su página y su descarga dicen que es un borrador, sin scripts ni estilos en atributos.
    page = c.get(f"/periodico/{eid}").text
    assert page.count(BAND) == 1 and "<title>BORRADOR · Lo que se compartió" in page and 'class="fp-wrap">\n' + BAND in page
    assert "style=" not in page and "<script" not in page and ".fp-draft{position:sticky" in page
    assert _filename(c.get(f"/periodico/{eid}", params={"download": "true"})) == "borrador_2026-10-01_a_2026-10-03.html"

    # Aprobada por la pantalla, entra al sitio sin banda: lo publicado es exactamente lo que se guardó al aprobar.
    body = {**APPROVE, "to": "sitio público"}
    check("ApproveRequest", body)
    r = c.post(f"/api/newspaper/editions/{eid}/approve", json=body)
    assert r.status_code == 200, r.text
    row = r.json()
    check("NewspaperEdition", row)
    assert row == _row(c, cid, eid) and row["status"] == "aprobada" and (row["hash_ok"], row["up_to_date"], row["in_site"]) == (True, True, True)
    page = c.get(f"/periodico/{eid}", params={"publico": 1}).text
    assert 'class="fp-draft"' not in page and "BORRADOR" not in page and 'class="fp-draft"' not in c.get(f"/periodico/{eid}").text
    assert _filename(c.get(f"/periodico/{eid}", params={"download": "true"})) == "periodico_2026-10-01_a_2026-10-03.html"
    site = newspaper.publish(con, cid, tmp_path / "sitio")
    assert site == {"files": ["index.html", "ediciones/2026-10-01_a_2026-10-03.html"], "skipped": []}
    written = (tmp_path / "sitio" / site["files"][1]).read_bytes()
    assert written == con.execute("SELECT approved_html FROM editions WHERE id=?", (eid,)).fetchone()[0] == page.encode()
    assert not any('class="fp-draft"' in f.read_text() for f in (tmp_path / "sitio").rglob("*.html"))
    # ?borrador=1 la manda a revisar con banda sin cambiarle el estado.
    forced = c.get(f"/periodico/{eid}", params={"borrador": 1})
    assert BAND in forced.text and "<title>BORRADOR · " in forced.text
    assert _filename(c.get(f"/periodico/{eid}", params={"borrador": 1, "download": "true"})) == "borrador_2026-10-01_a_2026-10-03.html"
    assert _row(c, cid, eid) == row
    con.close()


def test_published_bytes_are_the_approved_ones_and_damage_is_reported(client, tmp_path, monkeypatch, capsys):
    from community_lens import cli, newspaper_html
    from community_lens.db import connect
    c, cid = client
    con = connect()
    site_dir = tmp_path / "sitio"
    page_file = site_dir / "ediciones" / "2026-10-01_a_2026-10-03.html"
    a = _generate(c, cid)
    assert c.post(f"/api/newspaper/editions/{a}/approve", json=APPROVE).status_code == 200
    approved = con.execute("SELECT approved_html FROM editions WHERE id=?", (a,)).fetchone()[0]
    # Plantilla nueva: el sitio sigue publicando los bytes aprobados y la lista avisa que reaprobar los actualiza.
    monkeypatch.setattr(newspaper_html, "CSS", newspaper_html.CSS + "\n.fp-nueva{color:red}")
    assert newspaper.publish(con, cid, site_dir)["skipped"] == []
    assert page_file.read_bytes() == approved and b"fp-nueva" not in approved
    assert "fp-nueva" in c.get(f"/periodico/{a}").text  # la página de la app se dibuja con la plantilla de hoy
    row = _row(c, cid, a)
    assert (row["hash_ok"], row["up_to_date"], row["in_site"]) == (True, False, True)
    row = c.post(f"/api/newspaper/editions/{a}/approve", json=APPROVE).json()
    assert (row["hash_ok"], row["up_to_date"], row["in_site"]) == (True, True, True)
    newspaper.publish(con, cid, site_dir)
    assert b"fp-nueva" in page_file.read_bytes()

    # Dos aprobadas del mismo rango: se publica solo la más nueva.
    b = _generate(c, cid)
    assert c.post(f"/api/newspaper/editions/{b}/approve", json=APPROVE).status_code == 200
    assert (_row(c, cid, a)["in_site"], _row(c, cid, b)["in_site"]) == (False, True)
    # publish() borra de ediciones/ solo las páginas de rango que no escribió.
    (site_dir / "ediciones" / "2026-01-01_a_2026-01-07.html").write_text("vieja")
    (site_dir / "ediciones" / "notas.txt").write_text("mía")
    (site_dir / "ediciones" / "portada.html").write_text("mía")
    newspaper.publish(con, cid, site_dir)
    assert sorted(p.name for p in (site_dir / "ediciones").iterdir()) == ["2026-10-01_a_2026-10-03.html", "notas.txt", "portada.html"]

    # Los bytes de la más nueva cambiaron por fuera: no se escriben, el rango sale del sitio (no vuelve la vieja).
    con.execute("UPDATE editions SET approved_html = approved_html || ? WHERE id=?", (b"<!-- otro -->", b))
    site = newspaper.publish(con, cid, site_dir)
    assert site["files"] == ["index.html"] and [(s["id"], s["from"], s["to"]) for s in site["skipped"]] == [(b, "2026-10-01", "2026-10-03")]
    assert "hash" in site["skipped"][0]["reason"] and not page_file.exists()
    assert ((r := _row(c, cid, b))["hash_ok"], r["in_site"], _row(c, cid, a)["in_site"]) == (False, False, False)
    capsys.readouterr()
    assert cli.periodico(["--grupo-id", str(cid), "--solo-publicar", "--salida", str(site_dir)]) == 1
    assert f"la edición {b} (2026-10-01 a 2026-10-03) no se escribió" in capsys.readouterr().err
    # Cambiado a texto por SQL también se detecta, y reaprobar lo arregla.
    con.execute("UPDATE editions SET approved_html = CAST(approved_html AS TEXT) || 'x' WHERE id=?", (b,))
    assert _row(c, cid, b)["hash_ok"] is False
    assert c.post(f"/api/newspaper/editions/{b}/approve", json=APPROVE).json()["in_site"] is True
    assert newspaper.publish(con, cid, site_dir)["skipped"] == [] and page_file.exists()
    # Un registro de aprobación incompleto o ilegible (cambiado por SQL) es una aprobada dañada, nunca un 500.
    for raw in ("{}", '{"by": "Jaime", "via": "WhatsApp"}', "no es JSON", "[1]"):
        con.execute("UPDATE editions SET approval=? WHERE id=?", (raw, b))
        assert c.get("/api/newspaper/editions", params={"chat_id": cid}).status_code == 200, raw
        assert ((r := _row(c, cid, b))["hash_ok"], r["in_site"], r["approval"]["hash"]) == (False, False, ""), raw
        assert [s["id"] for s in newspaper.publish(con, cid, site_dir)["skipped"]] == [b], raw
    assert c.post(f"/api/newspaper/editions/{b}/approve", json=APPROVE).json()["hash_ok"] is True
    con.close()


def test_approval_is_recorded_and_validated(client):
    from datetime import UTC, datetime, timedelta

    from community_lens import edition
    from community_lens.db import connect
    c, cid = client
    eid = _generate(c, cid)
    for body in ({"by": "", "via": "WhatsApp"}, {"by": "   ", "via": "WhatsApp"}, {"by": "Jaime", "via": ""}, {"by": "Jaime", "via": "\n\t"},
                 {"by": "x" * 201, "via": "WhatsApp"}, {"by": "Jaime", "via": "y" * 201}, {**APPROVE, "to": "z" * 501}):
        r = c.post(f"/api/newspaper/editions/{eid}/approve", json=body)
        assert r.status_code == 422 and r.json()["error"] == "newspaper", (body, r.status_code)
        check("ApiErrorBody", r.json())
    assert "Falta quién aprueba" in c.post(f"/api/newspaper/editions/{eid}/approve", json={"by": " ", "via": "x"}).json()["detail"]
    assert _row(c, cid, eid)["status"] == "borrador" and _row(c, cid, eid)["approval"] is None
    for action in ("approve", "discard", "restore"):
        assert c.post(f"/api/newspaper/editions/99999/{action}", json=APPROVE).status_code == 404
    # Se guarda sin espacios sobrantes; el destino es opcional; 200 caracteres todavía entran.
    row = c.post(f"/api/newspaper/editions/{eid}/approve", json={"by": "  Jaime ", "via": "x" * 200}).json()
    approval = row["approval"]
    assert (approval["by"], approval["via"], approval["to"]) == ("Jaime", "x" * 200, None)
    at = datetime.strptime(approval["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert abs(datetime.now(UTC) - at) < timedelta(minutes=1)
    con = connect()
    stored = con.execute("SELECT approval, approved_html FROM editions WHERE id=?", (eid,)).fetchone()
    assert json.loads(stored["approval"]) == approval and approval["hash"] == edition.page_hash(stored["approved_html"])
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", approval["hash"])
    # La misma función sirve al comando: aprobar de nuevo reemplaza el registro.
    again = edition.approve(con, eid, "Otra persona", "correo", "sitio/")
    assert (again["by"], again["to"]) == ("Otra persona", "sitio/") and _row(c, cid, eid)["approval"] == again
    with pytest.raises(ValueError, match="Falta el canal"):
        edition.approve(con, eid, "Jaime", None)
    con.close()


def test_discard_hides_and_restore_brings_back_a_draft(client, tmp_path):
    from community_lens.db import connect
    c, cid = client
    con = connect()
    eid = _generate(c, cid)
    assert c.post(f"/api/newspaper/editions/{eid}/approve", json=APPROVE).status_code == 200
    assert newspaper.publish(con, cid, tmp_path / "sitio")["files"] == ["index.html", "ediciones/2026-10-01_a_2026-10-03.html"]
    r = c.post(f"/api/newspaper/editions/{eid}/discard")
    assert r.status_code == 200, r.text
    row = r.json()
    check("NewspaperEdition", row)
    assert (row["status"], row["hash_ok"], row["in_site"]) == ("descartada", None, False) and row["approval"]["by"] == "Jaime"
    assert newspaper.publish(con, cid, tmp_path / "sitio")["files"] == ["index.html"]
    assert list((tmp_path / "sitio" / "ediciones").iterdir()) == []
    assert BAND in c.get(f"/periodico/{eid}").text
    for action, why in (("discard", "ya está descartada"), ("approve", "recuperala antes")):
        r = c.post(f"/api/newspaper/editions/{eid}/{action}", json=APPROVE)
        assert r.status_code == 422 and why in r.json()["detail"], action
    row = c.post(f"/api/newspaper/editions/{eid}/restore").json()
    check("NewspaperEdition", row)
    assert (row["status"], row["in_site"]) == ("borrador", False) and row["approval"]["by"] == "Jaime"  # el registro no se toca
    assert c.post(f"/api/newspaper/editions/{eid}/restore").status_code == 422
    assert newspaper.publish(con, cid, tmp_path / "sitio")["files"] == ["index.html"]
    con.close()


def test_pending_draft_names_the_newest_and_counts_them(con):
    from conftest import do_import

    from community_lens import edition
    assert issubclass(newspaper.DraftPending, newspaper.NewspaperError) and not issubclass(newspaper.DraftPending, newspaper.NoNews)
    cid = do_import(con, "c.txt", data=_export("2026-10-20"), date_order="MDY")["chat_id"]
    assert newspaper.pending_draft(con, cid) is None
    a = edition.save(con, cid, {"from": "2026-10-01", "to": "2026-10-07", "editor": {}}, {})
    b = edition.save(con, cid, {"from": "2026-10-08", "to": "2026-10-14", "editor": {}}, {})
    got = newspaper.pending_draft(con, cid)
    assert got == {"id": b, "from": "2026-10-08", "to": "2026-10-14", "created_at": got["created_at"], "count": 2}
    edition.discard(con, b)
    assert newspaper.pending_draft(con, cid)["id"] == a and newspaper.pending_draft(con, cid)["count"] == 1
    con.execute("UPDATE editions SET status='aprobada' WHERE id=?", (a,))
    assert newspaper.pending_draft(con, cid) is None


def test_every_insert_into_editions_names_its_status():
    """El valor por defecto de status es 'heredada', solo para filas anteriores a la aprobación: ningún INSERT nuevo
    puede apoyarse en él, o una edición recién generada entraría al sitio sin que nadie la apruebe."""
    from pathlib import Path

    import community_lens
    inserts, named = 0, 0
    for f in Path(community_lens.__file__).parent.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        inserts += len(re.findall(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+editions\b", text, re.I))
        named += sum("status" in {c.strip() for c in cols.split(",")}
                     for cols in re.findall(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+editions\s*\(([^)]*)\)", text, re.I))
    assert inserts >= 1 and named == inserts



# ------------------------------------------ comando: aprobar, descartar, borrador y listar (#7) --

def _json(capsys, *args: str) -> tuple[int, dict, str]:
    """Corre el comando con --json: código, objeto de la salida estándar y mensajes para personas (stderr)."""
    from community_lens import cli
    code = cli.periodico([*args, "--json"])
    io = capsys.readouterr()
    return code, json.loads(io.out), io.err


def _usage_error(capsys, *args: str) -> str:
    """El comando rechaza los argumentos con código 2, antes de abrir la base."""
    from community_lens import cli
    with pytest.raises(SystemExit) as exit_:
        cli.periodico(list(args))
    assert exit_.value.code == 2, args
    return capsys.readouterr().err


def test_approve_on_the_fly_publishes_in_the_same_run(no_network, setenv, tmp_path, con, capsys):
    setenv(ANTHROPIC_API_KEY="k-demo")
    site = tmp_path / "sitio"
    code, res, err = _json(capsys, "--archivo", str(_folder(tmp_path, "2026-10-08") / "WhatsApp Chat 2026-10-08.txt"), "--grupo", "Semanal",
                           "--orden-fecha", "MDY", "--continuar", *APPROVE_NOW, "--salida", str(site))
    ed = res["edition"]
    assert (code, res["status"], ed["status"], ed["in_site"], ed["hash_ok"]) == (0, "generada", "aprobada", True, True)
    assert (ed["approval"]["by"], ed["approval"]["via"], ed["approval"]["to"]) == ("Prueba", "pytest", str(site))  # --destino: la de --salida
    assert ed["articles"] > 0 and res["site"] == {"dir": str(site), "files": 2, "skipped": []} and "aprobada al generarla" in err
    assert (site / "ediciones" / "2026-10-01_a_2026-10-07.html").exists()
    # Aprobar exige quién y por qué canal, y esos datos no van sin --aprobar.
    for args, why in ((["--aprobar", "--aprobado-por", "Jaime"], "exige --aprobado-por y --canal"),
                      (["--aprobar", "--canal", "WhatsApp"], "exige --aprobado-por y --canal"),
                      (["--aprobado-por", "Jaime"], "solo van con --aprobar"), (["--destino", "web"], "solo van con --aprobar"),
                      (["--solo-publicar", "--salida", str(site), *APPROVE_NOW], "--solo-publicar no genera ninguna")):
        assert why in _usage_error(capsys, "--grupo", "Semanal", "--dias", "3", *args)


def test_bad_approval_data_is_rejected_before_anything_runs(no_network, setenv, tmp_path, con, capsys):
    setenv(ANTHROPIC_API_KEY="k-demo")
    net, models = no_network
    export = str(_folder(tmp_path, "2026-10-08") / "WhatsApp Chat 2026-10-08.txt")
    for args, why in ((["--aprobado-por", "x" * 201, "--canal", "pytest"], "hasta 200 caracteres"),
                      (["--aprobado-por", "   ", "--canal", "pytest"], "Falta quién aprueba"),
                      (["--aprobado-por", "Prueba", "--canal", "\t"], "Falta el canal"),
                      (["--aprobado-por", "Prueba", "--canal", "pytest", "--destino", "z" * 501], "hasta 500 caracteres"),
                      (["--aprobado-por", "Prueba", "--canal", "pytest", "--salida", "s" * 501], "hasta 500 caracteres")):
        assert why in _usage_error(capsys, "--archivo", export, "--grupo", "Semanal", "--orden-fecha", "MDY", "--continuar", "--aprobar", *args)
    assert len(models.requests) == 0 and net.requests == []  # nada salió de la máquina
    assert con.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 0  # ni se importó


def test_command_acts_on_a_saved_edition_without_generating(no_network, setenv, tmp_path, con, capsys):
    from conftest import do_import
    setenv(ANTHROPIC_API_KEY="k-demo")
    net, models = no_network
    site = tmp_path / "sitio"
    assert _run(tmp_path, "2026-10-08") == 0
    cid = newspaper.find_chat(con, "Semanal")["id"]
    eid = newspaper.editions(con, cid)[0]["id"]
    calls, pages = len(models.requests), len(net.requests)
    capsys.readouterr()
    code, res, err = _json(capsys, "--grupo", "Semanal", "--edicion", str(eid), "--aprobar", "--aprobado-por", "Jaime", "--canal", "WhatsApp",
                           "--salida", str(site))
    assert (code, res["status"], res["import"], res["warnings"]) == (0, "aprobada", None, [])
    check("NewspaperEdition", res["edition"])
    assert res["edition"] == newspaper.editions(con, cid)[0] and res["edition"]["approval"]["to"] == str(site)
    assert res["site"]["files"] == 2 and (site / "ediciones" / "2026-10-01_a_2026-10-07.html").exists() and "Jaime" not in err
    # Ni el modelo ni las páginas: actuar no saca nada de la máquina.
    assert len(models.requests) == calls and len(net.requests) == pages
    code, res, _ = _json(capsys, "--grupo-id", str(cid), "--edicion", str(eid), "--aprobar", "--aprobado-por", "Jaime", "--canal", "correo",
                         "--destino", "web de la comunidad")
    assert (code, res["edition"]["approval"]["via"], res["edition"]["approval"]["to"], res["site"]) == (0, "correo", "web de la comunidad", None)
    code, res, _ = _json(capsys, "--grupo-id", str(cid), "--edicion", str(eid), "--aprobar", "--aprobado-por", "Jaime", "--canal", "correo")
    assert (code, res["edition"]["approval"]["to"]) == (0, None)  # sin --destino ni --salida no se inventa un destino
    assert "hasta 200 caracteres" in _usage_error(capsys, "--grupo-id", str(cid), "--edicion", str(eid), "--aprobar", "--aprobado-por", "x" * 201,
                                                  "--canal", "correo")
    # La edición tiene que ser del grupo indicado.
    other = do_import(con, "otro.txt", data=CHAT, date_order="MDY")["chat_id"]
    code, res, _ = _json(capsys, "--grupo-id", str(other), "--edicion", str(eid), "--descartar")
    assert (code, res["status"]) == (1, "error") and f"no es del grupo {other}" in res["detail"]
    assert newspaper.editions(con, cid)[0]["status"] == "aprobada"
    for args, why in ((["--continuar"], "no se combina con --continuar"), (["--dias", "3"], "no se combina con --dias"),
                      (["--archivo", "x.txt"], "no se combina con --archivo"), (["--solo-publicar", "--salida", "x"], "no hace falta --solo-publicar"),
                      ([], "--edicion necesita una acción")):
        assert why in _usage_error(capsys, "--grupo", "Semanal", "--edicion", str(eid), *args)


def test_discard_and_restore_by_command(no_network, setenv, tmp_path, con, capsys):
    setenv(ANTHROPIC_API_KEY="k-demo")
    assert _run(tmp_path, "2026-10-08", *APPROVE_NOW) == 0
    cid = newspaper.find_chat(con, "Semanal")["id"]
    first = newspaper.editions(con, cid)[0]["id"]
    capsys.readouterr()
    code, res, _ = _json(capsys, "--grupo", "Semanal", "--edicion", str(first), "--descartar")
    assert (code, res["status"], res["edition"]["status"], res["edition"]["in_site"]) == (0, "descartada", "descartada", False)
    # Una descartada no cuenta para --continuar: su período se vuelve a generar.
    assert _run(tmp_path, "2026-10-08") == 0
    assert "(2026-10-01 a 2026-10-07)" in capsys.readouterr().out
    code, res, _ = _json(capsys, "--grupo", "Semanal", "--edicion", str(first), "--recuperar")
    assert (code, res["status"], res["edition"]["status"], res["edition"]["approval"]["by"]) == (0, "recuperada", "borrador", "Prueba")
    code, res, _ = _json(capsys, "--grupo", "Semanal", "--edicion", str(first), "--recuperar")
    assert (code, res["status"]) == (1, "error") and "no está descartada" in res["detail"]
    for args, why in ((["--descartar"], "necesitan --edicion"), (["--recuperar"], "necesitan --edicion"),
                      (["--edicion", "1", "--descartar", "--recuperar"], "no se combinan"),
                      (["--edicion", "1", "--descartar", *APPROVE_NOW], "no se combinan")):
        assert why in _usage_error(capsys, "--grupo", "Semanal", *args)


def test_a_pending_draft_stops_continue(no_network, setenv, tmp_path, con, capsys):
    from community_lens import cli
    setenv(ANTHROPIC_API_KEY="k-demo")
    net, models = no_network
    assert _run(tmp_path, "2026-10-08", *APPROVE_NOW) == 0
    # Borradores hechos con un rango explícito (como desde la pantalla): ahí no rige el freno.
    assert cli.periodico(["--grupo", "Semanal", "--desde", "2026-10-01", "--hasta", "2026-10-03"]) == 0
    assert cli.periodico(["--grupo", "Semanal", "--desde", "2026-10-04", "--hasta", "2026-10-06"]) == 0
    cid = newspaper.find_chat(con, "Semanal")["id"]
    newest = newspaper.editions(con, cid)[0]["id"]
    calls, pages = len(models.requests), len(net.requests)
    capsys.readouterr()
    # Con la misma exportación sigue ganando "sin novedad": primero se mira si hay días nuevos.
    assert _run(tmp_path, "2026-10-08", "--json") == 3
    assert json.loads(capsys.readouterr().out)["status"] == "sin_novedad"
    assert _run(tmp_path, "2026-10-15", "--json") == 3
    res = json.loads(capsys.readouterr().out)
    assert (res["status"], res["edition"], res["site"]) == ("borrador_pendiente", None, None)
    assert f"La edición {newest} (2026-10-04 a 2026-10-06) sigue como borrador (hay 2 borradores" in res["detail"]
    assert f"--edicion {newest} --aprobar" in res["detail"] and f"--edicion {newest} --descartar" in res["detail"]
    assert len(models.requests) == calls and len(net.requests) == pages and len(newspaper.editions(con, cid)) == 3
    # Descartar o aprobar los borradores libera la corrida.
    assert cli.periodico(["--grupo", "Semanal", "--edicion", str(newest), "--descartar"]) == 0
    assert cli.periodico(["--grupo", "Semanal", "--edicion", str(newest - 1), *APPROVE_NOW]) == 0
    capsys.readouterr()
    assert _run(tmp_path, "2026-10-15") == 0
    assert "(2026-10-08 a 2026-10-14)" in capsys.readouterr().out


def test_draft_preview_and_listing(no_network, setenv, tmp_path, con, capsys):
    setenv(ANTHROPIC_API_KEY="k-demo")
    preview = tmp_path / "compartida" / "nueva" / "borrador.html"
    assert _run(tmp_path, "2026-10-08", "--borrador", str(preview)) == 0
    assert f"escrita en {preview}" in capsys.readouterr().out
    assert BAND in preview.read_text(encoding="utf-8") and [p.name for p in preview.parent.iterdir()] == ["borrador.html"]
    assert not list(tmp_path.rglob("index.html"))  # sin --salida no se escribe ningún sitio
    cid = newspaper.find_chat(con, "Semanal")["id"]
    eid = newspaper.editions(con, cid)[0]["id"]
    # De una aprobada también: es el preview para mandar a revisar de nuevo, y no le cambia el estado.
    approve_drafts(con)
    code, res, _ = _json(capsys, "--grupo", "Semanal", "--edicion", str(eid), "--borrador", str(tmp_path / "otra.html"))
    assert (code, res["status"], res["draft"], res["edition"]["status"]) == (0, "vista_previa", str(tmp_path / "otra.html"), "aprobada")
    assert BAND in (tmp_path / "otra.html").read_text(encoding="utf-8")
    assert "--solo-publicar no genera ninguna" in _usage_error(capsys, "--grupo", "Semanal", "--solo-publicar", "--salida", "x", "--borrador", "b.html")
    # --listar: una línea por edición, y en JSON la lista completa.
    assert _run(tmp_path, "2026-10-15") == 0
    from community_lens import cli
    capsys.readouterr()
    assert cli.periodico(["--grupo", "Semanal", "--listar"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2 and lines[0].startswith(f"{eid + 1}  2026-10-08 a 2026-10-14  borrador · fuera del sitio")
    assert lines[1].startswith(f"{eid}  2026-10-01 a 2026-10-07  aprobada por Prueba (pytest, 20") and lines[1].endswith(" · en el sitio")
    code, res, _ = _json(capsys, "--grupo", "Semanal", "--listar")
    assert (code, res["status"], res["edition"]) == (0, "listado", None) and [e["status"] for e in res["editions"]] == ["borrador", "aprobada"]
    check("NewspaperEdition", res["editions"], array=True)
    assert "--listar solo muestra" in _usage_error(capsys, "--grupo", "Semanal", "--listar", "--continuar")


def test_damaged_approved_edition_is_an_error_in_json(no_network, setenv, tmp_path, con, capsys):
    from community_lens import edition
    setenv(ANTHROPIC_API_KEY="k-demo")
    site = tmp_path / "sitio"
    assert _run(tmp_path, "2026-10-08", *APPROVE_NOW, "--salida", str(site)) == 0
    cid = newspaper.find_chat(con, "Semanal")["id"]
    eid = newspaper.editions(con, cid)[0]["id"]
    con.execute("UPDATE editions SET approved_html = approved_html || ? WHERE id=?", (b"<!-- otro -->", eid))
    capsys.readouterr()
    code, res, err = _json(capsys, "--grupo", "Semanal", "--solo-publicar", "--salida", str(site))
    assert (code, res["status"], res["site"]["files"]) == (1, "error", 1) and [s["id"] for s in res["site"]["skipped"]] == [eid]
    assert res["detail"].startswith(f"1 edición(es) no se escribieron en el sitio: la {eid} (2026-10-01 a 2026-10-07): {DAMAGED}")
    assert f"la edición {eid}" in err and [e["blocked_reason"] for e in edition.listing(con, cid)] == [DAMAGED]
    # Reaprobar por comando lo arregla en la misma corrida.
    code, res, _ = _json(capsys, "--grupo", "Semanal", "--edicion", str(eid), *APPROVE_NOW, "--salida", str(site))
    assert (code, res["status"], res["edition"]["hash_ok"], res["site"]["skipped"]) == (0, "aprobada", True, [])


# ------------------------------------------------ versión pública: el sitio no lleva nada del chat (#5) --

SECRET, GROUP = "CENTINELA-LECTURA-7f3a", "Grupo Centinela 9c1d"
LEAKS = (SECRET, GROUP, 'id="lecturas"', "En números")  # nada de esto puede llegar al sitio
DAMAGED = "dañada: no coincide con su hash; reaprobala para publicarla"
OLD_PUBLIC = "aprobada antes de la versión pública: reaprobala para publicarla"
OLD_READINGS = "contiene lecturas de la conversación: reaprobala para publicarla"


def sentinel_model(provider, system, user):
    if system == newspaper.READING_SYSTEM:
        return json.dumps({"parrafos": [f"{SECRET}: lo que se habló.", "Segundo párrafo."], "temas": [{"tema": "Datos", "mensajes": [1, 2]}]})
    return fake_model(provider, system, user)


@pytest.fixture
def sentinel(no_network, setenv):
    """Un grupo con nombre centinela cuya lectura lleva una frase centinela."""
    setenv(ANTHROPIC_API_KEY="k-demo")
    no_network[1].answer = sentinel_model
    with api_client() as c:
        pv = c.post("/api/imports/preview", params={"filename": "chat.txt"}, content=CHAT).json()
        cid = c.post("/api/imports/commit", json={"token": pv["token"], "file_name": "chat.txt", "date_order": "MDY", "chat_name": GROUP}).json()["chat_id"]
        yield c, cid


def _with_readings(c, cid) -> int:
    r = c.post("/api/newspaper/editions", json={"chat_id": cid, "from": "2026-10-01", "to": "2026-10-03", "editor": "anthropic",
                                                 "readers": ["anthropic"], "consent_chat": True})
    assert r.status_code == 200 and r.json()["readings"] == 1, r.text
    return r.json()["id"]


def _site_pages(site_dir) -> dict[str, str]:
    return {p.relative_to(site_dir).as_posix(): p.read_text(encoding="utf-8") for p in sorted(site_dir.rglob("*.html"))}


def _freeze_old_approval(con, eid, page: bytes, **extra) -> None:
    """Deja una aprobada como la dejaba la app antes de la versión pública: bytes dados, hash correcto y, salvo que
    extra diga otra cosa, un registro sin "public"."""
    from community_lens import edition
    approval = {"hash": edition.page_hash(page), "by": "Jaime", "via": "WhatsApp", "at": "2026-10-10T18:00:00Z", "to": None, **extra}
    con.execute("UPDATE editions SET status='aprobada', approval=?, approved_html=? WHERE id=?", (json.dumps(approval), page, eid))


def test_the_site_carries_nothing_from_the_conversation(sentinel, tmp_path):
    from community_lens import edition, newspaper_html
    from community_lens.db import connect
    c, cid = sentinel
    con = connect()
    eid = _with_readings(c, cid)
    ed = edition.load(con, eid)
    assert ed["chat_name"] == GROUP and SECRET in ed["readings"][0]["paragraphs"][0]
    row = c.post(f"/api/newspaper/editions/{eid}/approve", json=APPROVE).json()
    check("NewspaperEdition", row)
    assert (row["approval"]["public"], row["leaks_readings"], row["hash_ok"], row["up_to_date"], row["in_site"]) == (True, False, True, True, True)
    site = newspaper.publish(con, cid, tmp_path / "sitio")
    assert site == {"files": ["index.html", "ediciones/2026-10-01_a_2026-10-03.html"], "skipped": []}
    pages = _site_pages(tmp_path / "sitio")
    assert list(pages) == ["ediciones/2026-10-01_a_2026-10-03.html", "index.html"]
    for name, text in pages.items():
        for leak in LEAKS:
            assert leak not in text, (name, leak)
        assert "Titular " in text, name
    page = pages["ediciones/2026-10-01_a_2026-10-03.html"]
    assert "Compartido 1 oct" in page and "<title>Lo que se compartió · 1 al 3 de octubre</title>" in page and 'class="fp-sub"' not in page
    assert "Cronología reciente" in page and "Los enlaces y las fechas en que se compartieron salen del chat exportado." in page
    assert "<title>Lo que se compartió</title>" in pages["index.html"]  # la portada tampoco nombra al grupo
    # Lo que se aprobó y se hasheó es la versión pública, y es lo que se escribió.
    stored = con.execute("SELECT approval, approved_html FROM editions WHERE id=?", (eid,)).fetchone()
    written = (tmp_path / "sitio" / site["files"][1]).read_bytes()
    assert written == stored["approved_html"] == newspaper_html.render(ed, public=True).encode()
    assert json.loads(stored["approval"])["hash"] == edition.page_hash(written) and json.loads(stored["approval"])["public"] is True
    con.close()


def test_the_screen_shows_everything_and_publico_shows_what_is_published(sentinel):
    from community_lens import edition, newspaper_html
    from community_lens.db import connect
    c, cid = sentinel
    eid = _with_readings(c, cid)
    url = f"/periodico/{eid}"
    full = c.get(url).text
    assert all(x in full for x in (SECRET, GROUP, "En números", "Los enlaces y las cifras salen del chat exportado."))
    assert newspaper_html.READINGS_MARKER.decode() in full  # el marcador es exactamente lo que dibuja la plantilla
    public = c.get(url, params={"publico": 1}).text
    assert not any(leak in public for leak in LEAKS) and "Compartido" in public
    assert _filename(c.get(url, params={"publico": 1, "download": "true"})) == "borrador_publico_2026-10-01_a_2026-10-03.html"  # es borrador
    assert c.post(f"/api/newspaper/editions/{eid}/approve", json=APPROVE).status_code == 200
    names = {}
    for params in ({}, {"publico": 1}, {"borrador": 1}, {"publico": 1, "borrador": 1}):
        page = c.get(url, params=params).text
        assert (BAND in page) == ("borrador" in params) and (SECRET in page) == ("publico" not in params), params
        assert (GROUP in page) == ("En números" in page) == ("publico" not in params), params
        names[tuple(params)] = _filename(c.get(url, params={**params, "download": "true"}))
    assert names == {(): "periodico_2026-10-01_a_2026-10-03.html", ("publico",): "publico_2026-10-01_a_2026-10-03.html",
                     ("borrador",): "borrador_2026-10-01_a_2026-10-03.html",
                     ("publico", "borrador"): "borrador_publico_2026-10-01_a_2026-10-03.html"}
    # Sin cronología, la versión pública no tiene columna lateral: la rejilla pasa a una columna.
    con = connect()
    ed = edition.load(con, eid) | {"timeline": []}
    con.close()
    solo = newspaper_html.render(ed, public=True)
    assert '<section class="fp-lead-grid fp-solo" id="articulos">' in solo and "<aside" not in solo and ".fp-lead-grid.fp-solo{" in solo
    assert '<section class="fp-lead-grid" id="articulos">' in newspaper_html.render(ed) and "En números" in newspaper_html.render(ed)


def test_inherited_edition_with_readings_is_published_without_them(sentinel, tmp_path):
    from community_lens import edition
    from community_lens.db import connect
    c, cid = sentinel
    con = connect()
    data = edition.load(con, _with_readings(c, cid)) | {"from": "2026-09-01", "to": "2026-09-07"}
    eid = stored_edition(con, cid, data, {"pages_to": "Claude", "pages": 3})
    assert con.execute("SELECT status FROM editions WHERE id=?", (eid,)).fetchone()[0] == "heredada"
    assert SECRET in c.get(f"/periodico/{eid}").text
    site = newspaper.publish(con, cid, tmp_path / "sitio")
    assert site == {"files": ["index.html", "ediciones/2026-09-01_a_2026-09-07.html"], "skipped": []}
    for name, text in _site_pages(tmp_path / "sitio").items():
        assert not any(leak in text for leak in LEAKS), name
    assert _row(c, cid, eid)["leaks_readings"] is None and _row(c, cid, eid)["in_site"] is True
    con.close()


def test_approved_before_the_public_version_with_readings_waits_for_reapproval(sentinel, tmp_path, capsys):
    from community_lens import edition, newspaper_html
    from community_lens.db import connect
    c, cid = sentinel
    con = connect()
    site_dir = tmp_path / "sitio"
    eid = _with_readings(c, cid)
    _freeze_old_approval(con, eid, newspaper_html.render(edition.load(con, eid)).encode())
    row = _row(c, cid, eid)
    assert (row["approval"]["public"], row["hash_ok"], row["leaks_readings"], row["in_site"]) == (False, True, True, False)
    assert newspaper.publish(con, cid, site_dir)["skipped"] == [{"id": eid, "from": "2026-10-01", "to": "2026-10-03", "reason": OLD_PUBLIC}]
    capsys.readouterr()
    code, res, err = _json(capsys, "--grupo-id", str(cid), "--solo-publicar", "--salida", str(site_dir))
    assert code == 1 and [s["reason"] for s in res["site"]["skipped"]] == [OLD_PUBLIC] and OLD_PUBLIC in err
    assert not any(SECRET in t for t in _site_pages(site_dir).values()) and not (site_dir / "ediciones" / "2026-10-01_a_2026-10-03.html").exists()
    # Reaprobarla la arregla: queda pública y vuelve al sitio, sin la lectura.
    code, res, _ = _json(capsys, "--grupo-id", str(cid), "--edicion", str(eid), "--aprobar", "--aprobado-por", "Jaime", "--canal", "WhatsApp",
                         "--salida", str(site_dir))
    assert (code, res["edition"]["approval"]["public"], res["edition"]["leaks_readings"], res["edition"]["in_site"]) == (0, True, False, True)
    assert res["site"]["skipped"] == []
    pages = _site_pages(site_dir)
    assert "ediciones/2026-10-01_a_2026-10-03.html" in pages and not any(leak in t for t in pages.values() for leak in LEAKS)
    con.close()


def test_approved_before_the_public_version_without_readings_and_the_second_defense(sentinel, tmp_path):
    from community_lens import edition, newspaper_html
    from community_lens.db import connect
    c, cid = sentinel
    con = connect()
    plain = _generate(c, cid)
    _freeze_old_approval(con, plain, newspaper_html.render(edition.load(con, plain)).encode())
    row = _row(c, cid, plain)
    assert (row["approval"]["public"], row["hash_ok"], row["leaks_readings"], row["blocked_reason"], row["in_site"]) == (False, True, False, OLD_PUBLIC, False)
    assert [s["reason"] for s in newspaper.publish(con, cid, tmp_path / "sitio")["skipped"]] == [OLD_PUBLIC]
    headline = edition.load(con, plain)["lead"]["headline"]
    assert headline not in (tmp_path / "sitio" / "index.html").read_text(encoding="utf-8")  # salteada: la portada no la anuncia
    # Segunda defensa: aunque el registro diga public, bytes con lecturas no entran (otro rango, para no taparla).
    leaky = _generate(c, cid, "2026-10-02", "2026-10-03")
    data = edition.load(con, _with_readings(c, cid))
    _freeze_old_approval(con, leaky, newspaper_html.render(data).encode(), public=True)
    row = _row(c, cid, leaky)
    assert (row["approval"]["public"], row["hash_ok"], row["leaks_readings"], row["blocked_reason"], row["in_site"]) == (True, True, True, OLD_READINGS, False)
    site = newspaper.publish(con, cid, tmp_path / "sitio")
    assert {s["id"]: s["reason"] for s in site["skipped"]} == {plain: OLD_PUBLIC, leaky: OLD_READINGS} and site["files"] == ["index.html"]
    assert edition.blocked_reason(con.execute("SELECT * FROM editions WHERE id=?", (leaky,)).fetchone()) == OLD_READINGS
    # "public" tiene que ser el booleano true: el texto "true" (cambiado por SQL) no la abre, y queda fuera igual.
    stringy = _generate(c, cid, "2026-10-01", "2026-10-02")
    _freeze_old_approval(con, stringy, newspaper_html.render(data).encode(), public="true")
    row = _row(c, cid, stringy)
    assert (row["approval"]["public"], row["hash_ok"], row["leaks_readings"], row["blocked_reason"], row["in_site"]) == (False, True, True, OLD_PUBLIC, False)
    site = newspaper.publish(con, cid, tmp_path / "sitio")
    assert {s["id"]: s["reason"] for s in site["skipped"]} == {plain: OLD_PUBLIC, leaky: OLD_READINGS, stringy: OLD_PUBLIC} and site["files"] == ["index.html"]
    assert not any(leak in t for t in _site_pages(tmp_path / "sitio").values() for leak in LEAKS)
    con.close()


def test_draft_file_is_the_public_version_with_the_band(sentinel, tmp_path, capsys):
    """Caso 3: lo que --borrador deja en la carpeta compartida es lo que se publicaría, con banda; sin la lectura ni el
    nombre del grupo, y byte a byte lo mismo que ?publico=1&borrador=1."""
    c, cid = sentinel
    eid = _with_readings(c, cid)
    preview = tmp_path / "compartida" / "borrador.html"
    code, res, err = _json(capsys, "--grupo-id", str(cid), "--edicion", str(eid), "--borrador", str(preview))
    assert (code, res["status"], res["draft"]) == (0, "vista_previa", str(preview)) and "versión pública" in err
    page = preview.read_bytes()
    assert b'class="fp-draft"' in page and SECRET.encode() not in page and GROUP.encode() not in page
    assert page == c.get(f"/periodico/{eid}", params={"publico": 1, "borrador": 1}).content


def test_listing_says_why_an_approved_edition_waits_for_reapproval(sentinel, capsys):
    from community_lens import cli, edition, newspaper_html
    from community_lens.db import connect
    c, cid = sentinel
    con = connect()
    old = _generate(c, cid)
    _freeze_old_approval(con, old, newspaper_html.render(edition.load(con, old)).encode())  # sin "public"
    leaky = _generate(c, cid, "2026-10-02", "2026-10-03")
    _freeze_old_approval(con, leaky, newspaper_html.render(edition.load(con, _with_readings(c, cid))).encode(), public=True)
    con.close()
    capsys.readouterr()
    assert cli.periodico(["--grupo-id", str(cid), "--listar"]) == 0
    lines = {int(ln.split()[0]): ln for ln in capsys.readouterr().out.splitlines()}
    assert lines[old].endswith(f" · fuera del sitio · {OLD_PUBLIC}") and lines[leaky].endswith(f" · fuera del sitio · {OLD_READINGS}")


def test_the_public_name_heads_the_title_and_the_index(sentinel, setenv, tmp_path):
    from community_lens.db import connect
    c, cid = sentinel
    con = connect()
    eid = _with_readings(c, cid)
    assert c.post(f"/api/newspaper/editions/{eid}/approve", json=APPROVE).status_code == 200
    plain = _site_pages(newspaper.publish(con, cid, tmp_path / "sin") and tmp_path / "sin")
    for text in plain.values():
        assert '<h1 class="fp-title">Lo que se compartió</h1>' in text
    # Con nombre público: encabeza el título de la edición y de la portada, escapado; el del grupo sigue fuera.
    setenv(CL_PERIODICO_NOMBRE="FAIR.CR <b>")
    assert c.post(f"/api/newspaper/editions/{eid}/approve", json=APPROVE).status_code == 200
    named = _site_pages(newspaper.publish(con, cid, tmp_path / "con") and tmp_path / "con")
    for name, text in named.items():
        assert '<h1 class="fp-title">FAIR.CR &lt;b&gt; - Lo que se compartió</h1>' in text, name
        assert "<title>FAIR.CR &lt;b&gt; - Lo que se compartió" in text, name
        assert GROUP not in text and "FAIR.CR <b>" not in text, name
    # El subtítulo público va bajo el título en el sitio y reemplaza al nombre del grupo en la versión completa.
    setenv(CL_PERIODICO_SUBTITULO="Comunidad <General>")
    assert c.post(f"/api/newspaper/editions/{eid}/approve", json=APPROVE).status_code == 200
    subbed = _site_pages(newspaper.publish(con, cid, tmp_path / "sub") and tmp_path / "sub")
    for name, text in subbed.items():
        assert '<p class="fp-sub">Comunidad &lt;General&gt;</p>' in text and GROUP not in text, name
    full = c.get(f"/periodico/{eid}").text
    assert '<p class="fp-sub">Comunidad &lt;General&gt;</p>' in full and f'<p class="fp-sub">{GROUP}' not in full


def test_social_posts_carry_their_logo_and_empty_ones_go_to_a_footer_line():
    from community_lens import edition, newspaper_html
    from community_lens.brand_icons import LOGOS
    ed = edition.upgrade(json.loads((__import__("conftest").FIX / "edition_v1.json").read_text(encoding="utf-8"))["data"])
    base = {"account": "", "text": "La plataforma no entregó contenido público de esta publicación.", "shared": "2026-10-02",
            "video": False, "preview": False}
    ed["socials"] = [{**base, "platform": "Instagram", "url": "https://www.instagram.com/p/abc/", "text": "Texto del post", "preview": True},
                     {**base, "platform": "Facebook", "url": "https://www.facebook.com/share/p/xyz/"},
                     {**base, "platform": "LinkedIn", "url": "https://www.linkedin.com/posts/x"}]
    for public in (False, True):
        page = newspaper_html.render(ed, public=public)
        body = page.split("</style>")[1]
        cards, also = body.split('<p class="fp-also">')
        # Con contenido: tarjeta con el logo de la red en la etiqueta, y sin línea de cuenta si no se conoce.
        assert f'<path fill="#{LOGOS["Instagram"][0]}" d="{LOGOS["Instagram"][1]}"/>' in cards and "Texto del post" in cards
        assert "Cuenta no disponible" not in body and "fp-card-who" not in cards.split('id="redes"')[1].split("fp-card-tool")[0]
        # Sin contenido: ni tarjeta ni texto de relleno; una línea con red, fecha y enlace.
        assert "facebook.com" not in cards and "linkedin.com" not in cards and "La plataforma no entregó" not in body
        assert also.startswith("<span>También se compartió:</span>") and 'href="https://www.facebook.com/share/p/xyz/"' in also
        assert f'd="{LOGOS["Facebook"][1]}"' in also and "Facebook · 2 oct" in also and "💼" in also and "LinkedIn · 2 oct" in also
        assert "style=" not in body
    # Si ninguna entregó nada, no queda una fila vacía.
    ed["socials"], ed["tools"] = ed["socials"][1:], []
    assert '<div class="fp-row">' not in newspaper_html.render(ed) and "También se compartió:" in newspaper_html.render(ed)