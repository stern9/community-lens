"""Periódico: selección de enlaces, seudonimización, armado y contrato. Sin red y sin modelos reales."""
import pytest
from fastapi.testclient import TestClient

from community_lens import llm, newspaper
from test_contract import check

CHAT = """[10/1/26, 9:00:00 AM] ~ Ana Demo: Miren esta nota https://example.org/notas/centros-de-datos
[10/1/26, 9:05:00 AM] +506 5555 0202: Llamen a Ana Demo al +506 5555 0909 o escriban a ana@example.org
[10/2/26, 8:00:00 AM] +506 5555 0202: https://example.net/analisis/carrera-ia?utm_source=x
[10/2/26, 8:10:00 AM] ~ Ana Demo: video https://youtu.be/abc123 y la herramienta https://tool.example.com/
[10/3/26, 7:00:00 AM] ~ Ana Demo: únanse https://chat.whatsapp.com/XXXX y lean https://caido.example.org/nota/rota
""".encode()

PAGE = "<html><head><title>{t}</title><meta property='og:site_name' content='Medio Demo'></head><body><nav>menú</nav><p>" + "Texto del artículo. " * 30 + "</p><script>alert(1)</script></body></html>"


def fake_get(url: str):
    if "caido" in url:
        raise newspaper.NewspaperError("HTTP 404")
    if "oembed" in url:
        return url, '{"title": "Video demo", "author_name": "Canal Demo"}'
    return url, PAGE.format(t="Título " + url.rsplit("/", 1)[-1])


def fake_json(provider, system, user):
    if system is newspaper.READING_SYSTEM:
        return {"parrafos": ["Primer párrafo.", "Segundo párrafo."], "temas": [{"tema": "Datos", "mensajes": [1, 2, 2, 999]}, {"tema": "Otro", "mensajes": [2, 3]}]}
    ids = {int(line.split()[2]): line for line in user.splitlines() if line.startswith("=== id ")}
    art = [i for i, l in ids.items() if "ARTÍCULO" in l]
    return {"hilo": "Un hilo", "principal": art[0],
            "articulos": [{"id": i, "titular": f"Titular {i} <b>", "medio": "Medio", "resumen": ["Una oración.", "https://inventado.example/x"]} for i in art]
                         + [{"id": 4242, "titular": "Inventado", "resumen": ["x"]}],
            "redes": [{"id": i, "cuenta": "Canal Demo", "texto": "Un video.", "video": True} for i, l in ids.items() if "RED SOCIAL" in l],
            "sitios": [{"id": i, "nombre": "Tool", "texto": "Un sitio."} for i, l in ids.items() if "SITIO" in l],
            "cronologia": [{"fecha": "1 oct", "texto": "Algo pasó.", "fuente": "Medio"}]}


@pytest.fixture
def client(monkeypatch):
    from community_lens.api import app
    monkeypatch.setattr(newspaper, "http_get", fake_get)
    monkeypatch.setattr(llm, "complete_json", fake_json)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-demo")
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


def test_preview_sends_nothing_and_matches_types(client, monkeypatch):
    c, cid = client
    monkeypatch.setattr(newspaper, "http_get", lambda url: pytest.fail("la vista previa no debe descargar nada"))
    monkeypatch.setattr(llm, "complete_json", lambda *a: pytest.fail("la vista previa no debe llamar a un modelo"))
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


def test_no_provider_configured(client, monkeypatch):
    c, cid = client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "k2")  # key sin modelo: no se puede usar
    p = c.get("/api/newspaper/preview", params={"chat_id": cid, "from": "2026-10-01", "to": "2026-10-03"}).json()
    assert p["providers"] == [] and p["missing_model"] == ["OpenAI"]
    r = c.post("/api/newspaper/editions", json={"chat_id": cid, "from": "2026-10-01", "to": "2026-10-03", "editor": "openai"})
    assert r.status_code == 502 and "no está configurado" in r.json()["detail"]


def test_auto_uses_whatever_is_configured(client, monkeypatch):
    c, cid = client
    monkeypatch.setenv("OPENAI_API_KEY", "k2")
    monkeypatch.setenv("OPENAI_MODEL", "modelo-demo")
    monkeypatch.setenv("OPENAI_LABEL", "Otro")
    monkeypatch.setenv("GEMINI_API_KEY", "")  # sin key: no cuenta

    def claude_down(provider, system, user):
        if provider.name == "anthropic" and system is newspaper.EDITOR_SYSTEM:
            raise llm.LLMError("Anthropic respondió HTTP 529.")
        return fake_json(provider, system, user)
    monkeypatch.setattr(llm, "complete_json", claude_down)

    body = {"chat_id": cid, "from": "2026-10-01", "to": "2026-10-03", "editor": "auto", "readers": ["all"], "consent_chat": True}
    res = c.post("/api/newspaper/editions", json=body).json()
    check("NewspaperResult", res)
    # Redacta el siguiente configurado, queda dicho quién recibió qué, y cada modelo disponible da su lectura.
    assert res["sent"]["pages_to"] == "Otro" and res["sent"]["pages_tried"] == ["Claude"]
    assert res["sent"]["chat_to"] == ["Claude", "Otro"] and res["readings"] == 2
    assert any("Claude no pudo redactar" in w for w in res["warnings"])
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


def test_headless_command_imports_generates_and_publishes(tmp_path, monkeypatch, capsys):
    from community_lens import cli
    monkeypatch.setattr(newspaper, "http_get", fake_get)
    monkeypatch.setattr(llm, "complete_json", fake_json)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-demo")
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
