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
    assert (site / "index.html").exists() and (site / "ediciones" / "2026-10-01_a_2026-10-03.html").exists()
    index = (site / "index.html").read_text()
    assert 'href="ediciones/2026-10-01_a_2026-10-03.html"' in index and "<script" not in index
    # Correrlo otra vez con el mismo archivo no duplica mensajes y deja una sola página por rango.
    assert cli.periodico(args) == 0
    assert "ya estaba importado" in capsys.readouterr().out
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
    assert "La visión de" not in old_page.text and "Cuenta no disponible" in old_page.text and "sin vista previa" not in old_page.text

    listed = c.get("/api/newspaper/editions", params={"chat_id": cid}).json()
    check("NewspaperEdition", listed, array=True)
    assert [e["id"] for e in listed] == [b, a] and listed[0]["sent"]["pages_tried"] == []
    files = newspaper.publish(con, cid, "Demo", tmp_path / "sitio")
    assert files == ["index.html", "ediciones/2026-10-01_a_2026-10-03.html", "ediciones/2026-09-01_a_2026-09-07.html"]
    assert (tmp_path / "sitio" / files[1]).read_text(encoding="utf-8") == page.text
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
    edition.save(con, cid, {"from": "2026-10-03", "to": "2026-10-05", "editor": {}}, {})  # cuenta la que llega más lejos
    assert newspaper.continue_range(con, cid) == ("2026-10-10", "2026-10-19")
    edition.save(con, cid, {"from": "2026-10-10", "to": "2026-10-19", "editor": {}}, {})
    with pytest.raises(newspaper.NoNews, match="Sin novedad: la última edición ya cubre hasta el 2026-10-19"):
        newspaper.continue_range(con, cid)


def test_continue_leaves_no_gaps_or_repeats(no_network, setenv, tmp_path, con, capsys):
    setenv(ANTHROPIC_API_KEY="k-demo")
    # Una semana, otra, se salta una, y dos exportaciones seguidas.
    assert _run(tmp_path, "2026-10-08") == 0
    assert _run(tmp_path, "2026-10-15") == 0
    assert _run(tmp_path, "2026-10-29") == 0
    out = capsys.readouterr().out
    assert "(2026-10-01 a 2026-10-07)" in out and "(2026-10-08 a 2026-10-14)" in out and "(2026-10-15 a 2026-10-28)" in out
    # Al día siguiente solo hay un día completo nuevo con 2 artículos: no alcanza y queda para la próxima.
    assert _run(tmp_path, "2026-10-30") == 3
    assert "hacen falta 3" in capsys.readouterr().out
    assert _run(tmp_path, "2026-10-31") == 0
    assert "(2026-10-29 a 2026-10-30)" in capsys.readouterr().out
    cid = newspaper.find_chat(con, "Semanal")["id"]
    days = _covered(con, cid)
    assert len(days) == len(set(days)) == 30 and (days[0], days[-1]) == ("2026-10-01", "2026-10-30")


def test_same_export_twice_does_not_call_the_model_again(no_network, setenv, tmp_path, con, capsys):
    setenv(ANTHROPIC_API_KEY="k-demo")
    net, models = no_network
    assert _run(tmp_path, "2026-10-08") == 0
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
    assert res["site"] == {"dir": str(site), "files": 2} and isinstance(res["warnings"], list)
    assert "Exportación más nueva de la carpeta" in err and "Edición" in err and "Sitio escrito" in err
    # Ninguna clave trae texto del chat: ni enlaces ni nombres.
    out = json.dumps(res)
    assert "example.org" not in out and "Ana" not in out and "lean" not in out
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
    assert "quedó guardada" in out["detail"] and "--solo-publicar" in out["detail"] and "No se pudo:" in io.err
    assert len(newspaper.editions(con, newspaper.find_chat(con, "Semanal")["id"])) == 1


def test_solo_publicar_needs_salida_and_excludes_continuar(client, capsys):
    from community_lens import cli
    for args in (["--grupo", "Demo", "--solo-publicar"], ["--grupo", "Demo", "--solo-publicar", "--salida", "x", "--continuar"]):
        with pytest.raises(SystemExit) as exit_:
            cli.periodico(args)
        assert exit_.value.code == 2 and "necesita --salida y no se combina con --continuar" in capsys.readouterr().err
