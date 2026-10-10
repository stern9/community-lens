"""Periódico: selección de enlaces, seudonimización, armado y contrato. Sin red y sin modelos reales:
se reemplaza solo quién abre las URLs (newspaper.NET) y quién envía a los proveedores (llm.TRANSPORT)."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from community_lens import llm, newspaper
from conftest import FakeLLM, FakeNet
from test_contract import check

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
    from community_lens.api import app
    setenv(ANTHROPIC_API_KEY="k-demo")
    with TestClient(app) as c:
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
    chat = """[10/1/26, 9:00:00 AM] ~ Ana Demo: https://example.org/notas/uno?utm_source=boletin
[10/2/26, 9:00:00 AM] ~ Beto Demo: https://www.example.org/notas/uno/
[10/2/26, 9:30:00 AM] ~ Beto Demo: https://example.org/notas/dos
[10/3/26, 9:00:00 AM] ~ Ana Demo: https://sitio.example/?utm_source=x&utm_medium=y
[10/3/26, 9:10:00 AM] ~ Beto Demo: https://sitio.example/
[10/3/26, 9:20:00 AM] ~ Ana Demo: https://example.org/buscar?utm_source=x&q=datos
""".encode()
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
    from community_lens import edition
    from community_lens.db import connect
    from conftest import FIX
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
