"""Descarga de páginas para el periódico. Sin red: solo se reemplaza quién resuelve nombres y quién abre la conexión."""
import pytest

from community_lens import newspaper
from conftest import FakeNet

HTML = {"Content-Type": "text/html; charset=utf-8"}


class RealDnsNoSend(newspaper.Net):
    """Resolución real (direcciones literales y localhost no salen a la red) y ninguna solicitud permitida."""

    def get(self, parts, ip):
        pytest.fail(f"no debe emitirse ninguna solicitud ({parts.geturl()})")


@pytest.mark.parametrize("url", ["http://localhost:8765", "http://127.0.0.1:8765/api/chats", "http://10.0.0.1", "http://192.168.1.1/admin",
                                 "http://169.254.169.254/latest", "http://[::1]/", "file:///etc/hosts", "ftp://93.184.216.34/x"])
def test_non_public_address_is_rejected_before_any_request(url):
    with pytest.raises(newspaper.NewspaperError, match="dirección no permitida"):
        newspaper.http_get(url, RealDnsNoSend())


@pytest.mark.parametrize("location", ["http://127.0.0.1/", "http://localhost:8765/api/chats", "http://192.168.0.1/", "//10.0.0.1/x",
                                      "http://interno.example/", "file:///etc/hosts"])
def test_redirect_to_local_network_is_rejected_without_second_request(location):
    net = FakeNet(lambda url: (302, {"Location": location}, ""), dns={"interno.example": ["10.1.2.3"]})
    with pytest.raises(newspaper.NewspaperError, match="redirección no permitida"):
        newspaper.http_get("http://93.184.216.34/nota", net)
    assert net.requests == [("http://93.184.216.34/nota", "93.184.216.34")]   # solo la primera


def test_each_hop_is_checked_and_relative_redirects_are_followed():
    pages = {"https://medio.example/a": (301, {"Location": "/b?x=1"}, ""),
             "https://medio.example/b?x=1": (302, {"Location": "https://otro.example/c"}, ""),
             "https://otro.example/c": (200, HTML, "<p>hola</p>")}
    net = FakeNet(pages.__getitem__, dns={"otro.example": ["93.184.216.35"]})
    assert newspaper.http_get("https://medio.example/a", net) == ("https://otro.example/c", "<p>hola</p>")
    assert [ip for _, ip in net.requests] == ["93.184.216.34", "93.184.216.34", "93.184.216.35"]
    loop = FakeNet(lambda url: (302, {"Location": url + "x"}, ""))
    with pytest.raises(newspaper.NewspaperError, match="demasiadas redirecciones"):
        newspaper.http_get("https://medio.example/a", loop)
    assert len(loop.requests) == newspaper.MAX_REDIRECTS + 1


def test_connection_goes_to_the_address_that_was_checked():
    """Un nombre con alguna dirección privada se rechaza entero; si pasa, se conecta a la IP verificada."""
    mixed = FakeNet(lambda url: pytest.fail("no debe conectarse"), dns={"mixto.example": ["93.184.216.34", "127.0.0.1"], "nada.example": []})
    for host in ("mixto.example", "nada.example"):
        with pytest.raises(newspaper.NewspaperError, match="dirección no permitida"):
            newspaper.http_get(f"https://{host}/", mixed)
    net = FakeNet(lambda url: (200, HTML, "ok"), dns={"medio.example": ["93.184.216.40", "93.184.216.41"]})
    newspaper.http_get("https://medio.example/x", net)
    assert net.requests == [("https://medio.example/x", "93.184.216.40")]


def test_pinned_connection_does_not_resolve_the_name_again(monkeypatch):
    seen = []
    monkeypatch.setattr(newspaper.socket, "create_connection", lambda addr, timeout: seen.append(addr) or (_ for _ in ()).throw(OSError("sin red en pruebas")))
    monkeypatch.setattr(newspaper.socket, "getaddrinfo", lambda *a, **k: pytest.fail("no debe volver a resolver el nombre"))
    with pytest.raises(OSError):
        newspaper.Net().get(newspaper.urlsplit("http://medio.example:8080/x"), "93.184.216.34")
    assert seen == [("93.184.216.34", 8080)]


class FakeSocket:
    """Socket de mentira: guarda lo que http.client envía y le devuelve una respuesta fija."""

    def __init__(self, response: bytes):
        self.response, self.sent = response, b""

    def sendall(self, data):
        self.sent += data

    def makefile(self, mode, *a, **k):
        import io
        return io.BytesIO(self.response)

    def close(self):
        pass


def test_request_on_the_wire_and_body_is_capped(monkeypatch):
    big = b"x" * (newspaper.PAGE_BYTES + 5000)
    sock = FakeSocket(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=latin-1\r\nContent-Length: %d\r\n\r\n" % len(big) + big)
    monkeypatch.setattr(newspaper.socket, "create_connection", lambda addr, timeout: sock)
    status, headers, body = newspaper.Net().get(newspaper.urlsplit("http://medio.example/nota/año?utm_x=1&q=a b#frag"), "93.184.216.34")
    assert status == 200 and headers.get_content_charset() == "latin-1" and len(body) == newspaper.PAGE_BYTES
    head = sock.sent.decode("ascii")
    assert head.startswith("GET /nota/a%C3%B1o?utm_x=1&q=a%20b HTTP/1.1\r\n")      # sin fragmento y sin caracteres crudos
    assert "\r\nHost: medio.example\r\n" in head and "\r\nUser-Agent: Mozilla" in head and "93.184.216.34" not in head


def test_download_errors_and_size_limit():
    with pytest.raises(newspaper.NewspaperError, match="HTTP 404"):
        newspaper.http_get("https://medio.example/no", FakeNet(lambda url: (404, HTML, "no está")))
    with pytest.raises(newspaper.NewspaperError, match="HTTP 304"):                 # una redirección sin destino no es una página
        newspaper.http_get("https://medio.example/x", FakeNet(lambda url: (304, {}, "")))

    def down(url):
        raise ConnectionResetError("corte")
    with pytest.raises(newspaper.NewspaperError, match="sin respuesta"):
        newspaper.http_get("https://medio.example/x", FakeNet(down))
    with pytest.raises(newspaper.NewspaperError, match="sin respuesta"):            # juego de caracteres que no existe
        newspaper.http_get("https://medio.example/x", FakeNet(lambda url: (200, {"Content-Type": "text/html; charset=nope"}, "x")))
    # Nunca se procesa más que el límite, aunque quien abre la conexión entregue de más; y el texto limpio también se recorta.
    huge = "<p>" + "palabra " * (newspaper.PAGE_BYTES // 4) + "</p>"
    assert len(huge) > newspaper.PAGE_BYTES
    _, body = newspaper.http_get("https://medio.example/x", FakeNet(lambda url: (200, HTML, huge)))
    assert len(body) == newspaper.PAGE_BYTES
    page = newspaper.read_page({"url": "https://medio.example/x", "kind": "article"}, FakeNet(lambda url: (200, HTML, huge)))
    assert page["status"] == "ok" and len(page["text"]) == newspaper.PAGE_CHARS
    latin = FakeNet(lambda url: (200, {"Content-Type": "text/html; charset=iso-8859-1"}, "<p>año</p>".encode("latin-1")))
    assert newspaper.http_get("https://medio.example/x", latin)[1] == "<p>año</p>"


def test_read_page_never_raises_and_says_why():
    article = {"url": "https://medio.example/nota", "kind": "article"}
    ok = newspaper.read_page(article, FakeNet(lambda url: (200, HTML, "<title> Una  nota </title><nav>menú</nav><p>" + "Texto. " * 60 + "</p><script>x()</script>")))
    assert (ok["status"], ok["title"], ok["reason"]) == ("ok", "Una nota", None) and "menú" not in ok["text"] and "x()" not in ok["text"]
    for routes, reason in ((lambda url: (200, HTML, "<p>corto</p>"), "la página no trae texto legible"),
                           (lambda url: (403, HTML, "muro de pago"), "HTTP 403"),
                           (lambda url: (302, {"Location": "http://127.0.0.1:8765/"}, ""), "redirección no permitida")):
        bad = newspaper.read_page(article, FakeNet(routes))
        assert (bad["status"], bad["text"], bad["reason"]) == ("failed", "", reason)
    assert newspaper.read_page({"url": "http://192.168.1.10/panel", "kind": "tool"}, FakeNet(lambda url: pytest.fail("no"))) ["reason"] == "dirección no permitida"


def social(url, routes):
    net = FakeNet(routes)
    return newspaper.read_page({"url": url, "kind": "social"}, net), [u for u, _ in net.requests]


def test_social_card_for_x_uses_the_public_embed_endpoint():
    tweet = '{"text": "Un anuncio", "created_at": "2026-10-01T10:00:00Z", "user": {"name": "Medio Demo", "screen_name": "mediodemo"}, "mediaDetails": [{}]}'
    page, asked = social("https://x.com/mediodemo/status/1234567890?s=20", lambda url: (200, {}, tweet))
    assert asked == ["https://cdn.syndication.twimg.com/tweet-result?id=1234567890&token=a"]      # nunca la página de X
    assert page["status"] == "ok" and page["title"] == "X: Un anuncio"
    assert page["text"].splitlines() == ["[plataforma] X", "[texto] Un anuncio", "[cuenta] Medio Demo (@mediodemo)",
                                         "[fecha] 2026-10-01T10:00:00Z", "[adjunto] incluye video o imagen"]
    assert social("https://x.com/mediodemo", lambda url: pytest.fail("no"))[0]["reason"] == "no es una publicación"
    assert social("https://twitter.com/a/status/1", lambda url: (200, {}, "{}"))[0]["reason"] == "la plataforma no entregó contenido público"
    assert social("https://twitter.com/a/status/1", lambda url: (200, {}, "<html>"))[0]["reason"] == "no se pudo leer"
    assert social("https://twitter.com/a/status/1", lambda url: (404, {}, ""))[0]["reason"] == "HTTP 404"


def test_social_card_for_youtube_and_tiktok():
    def youtube(url):
        if "oembed" in url:
            return 200, {}, '{"title": "Charla demo", "author_name": "Canal Demo"}'
        return 200, HTML, '<meta property="og:description" content="De qué trata la charla">'
    page, asked = social("https://youtu.be/abc123", youtube)
    assert asked == ["https://www.youtube.com/oembed?format=json&url=https%3A%2F%2Fyoutu.be%2Fabc123", "https://youtu.be/abc123"]
    assert page["text"].splitlines() == ["[plataforma] YouTube", "[titulo] Charla demo", "[canal] Canal Demo", "[descripcion_truncada] De qué trata la charla"]
    # Sin la descripción (la página del video no responde) alcanza con lo del oembed.
    partial = social("https://youtu.be/abc123", lambda url: youtube(url) if "oembed" in url else (429, {}, ""))[0]
    assert partial["status"] == "ok" and "descripcion" not in partial["text"]

    def tiktok(url):
        if url.startswith("https://vt.tiktok.com/"):
            return 301, {"Location": "https://www.tiktok.com/@cuenta.demo/video/99?lang=es"}, ""
        if "oembed" in url:
            return 200, {}, '{"title": "Un baile", "author_name": ""}'
        return 200, HTML, "<title>TikTok</title>"
    page, asked = social("https://vt.tiktok.com/ZSabc/", tiktok)
    assert asked[-1] == "https://www.tiktok.com/oembed?url=https%3A%2F%2Fwww.tiktok.com%2F%40cuenta.demo%2Fvideo%2F99"
    assert page["text"].splitlines() == ["[plataforma] TikTok", "[texto] Un baile", "[cuenta] @cuenta.demo"]
    fallback = social("https://vt.tiktok.com/ZSabc/", lambda url: tiktok(url) if "oembed" not in url else (400, {}, ""))[0]
    assert fallback["status"] == "ok" and "[cuenta] @cuenta.demo" in fallback["text"] and "no estuvo disponible" in fallback["text"]


def test_social_card_for_other_platforms_reads_only_public_metadata():
    html = '<title>Instagram</title><meta property="og:description" content="Una foto del evento"><p>Iniciá sesión para ver más</p>'
    page, asked = social("https://www.instagram.com/p/abc/", lambda url: (200, HTML, html))
    assert asked == ["https://www.instagram.com/p/abc/"]
    assert page["text"].splitlines() == ["[plataforma] Instagram", "[texto] Una foto del evento"]      # el título genérico no aporta
    wall = social("https://www.linkedin.com/posts/x", lambda url: (200, HTML, "<title>Sign Up | LinkedIn</title><p>Unite</p>"))[0]
    assert (wall["status"], wall["reason"]) == ("failed", "la plataforma no entregó contenido público")


def test_https_is_verified_against_the_name_while_connecting_to_the_checked_address(monkeypatch):
    """Conectarse a la IP ya verificada no relaja TLS: el certificado y el SNI siguen siendo los del nombre."""
    seen = {}

    class Context:
        def wrap_socket(self, sock, server_hostname):
            seen["tls"] = (sock, server_hostname)
            return sock

    sock = FakeSocket(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    monkeypatch.setattr(newspaper.socket, "create_connection", lambda addr, timeout: seen.setdefault("addr", addr) and sock)
    monkeypatch.setattr(newspaper.ssl, "create_default_context", lambda: Context())
    status, _, body = newspaper.Net().get(newspaper.urlsplit("https://medio.example/x"), "93.184.216.34")
    assert (status, body) == (200, b"ok") and seen == {"addr": ("93.184.216.34", 443), "tls": (sock, "medio.example")}
    assert "\r\nHost: medio.example\r\n" in sock.sent.decode("ascii")


def test_next_verified_address_is_tried_when_one_does_not_connect(monkeypatch):
    """Un nombre con IPv6 e IPv4 en una red sin ruta IPv6: se prueba la siguiente dirección ya verificada."""
    seen = []

    def no_route(addr, timeout):
        seen.append(addr[0])
        raise OSError("sin ruta")
    monkeypatch.setattr(newspaper.socket, "create_connection", no_route)

    class DualStack(FakeNet):
        def get(self, parts, ip):
            if ":" in ip:
                return newspaper.Net.get(self, parts, ip)  # el connect real, que falla
            return super().get(parts, ip)
    net = DualStack(lambda url: (200, HTML, "<p>hola</p>"), dns={"medio.example": ["2606:4700::1111", "93.184.216.34"]})
    assert newspaper.http_get("https://medio.example/a", net) == ("https://medio.example/a", "<p>hola</p>")
    assert seen == ["2606:4700::1111"] and [ip for _, ip in net.requests] == ["93.184.216.34"]
    solo_v6 = DualStack(lambda url: pytest.fail("no hay a dónde pedirlo"), dns={"medio.example": ["2606:4700::1111"]})
    with pytest.raises(newspaper.NewspaperError, match="sin respuesta"):
        newspaper.http_get("https://medio.example/a", solo_v6)
