from conftest import do_import

from community_lens import resources


def chat_with_docs(con):
    lines = [
        "[5/26/26, 17:35:00] Dana Demo: \u200eManual-Demo-v1.4.docx \u200edocument omitted",
        "[5/28/26, 22:10:00] Dana Demo: \u200eManual-Demo-v1.4.docx \u200edocument omitted",       # reenvío
        "[6/12/26, 09:06:00] Jota Demo: \u200eManual-Demo-v1.5.pdf \u200edocument omitted",
        "[6/12/26, 14:19:00] Jota Demo: \u200eManual-Demo-v1_6.pdf • \u200e47 pages \u200edocument omitted",  # guion bajo y páginas
        "[6/20/26, 10:00:00] Dana Demo: \u200e2606.27334v1.pdf \u200edocument omitted",             # no es una versión del manual
        "[6/21/26, 10:00:00] Dana Demo: \u200einforme (1).pdf \u200edocument omitted",
        "[6/22/26, 10:00:00] Jota Demo: \u200einforme.pdf \u200edocument omitted",
        "[6/23/26, 10:00:00] Dana Demo: mirá https://example.org/a y https://example.org/a/",
        "[6/24/26, 10:00:00] Jota Demo: otra vez https://example.org/a",
        "[6/25/26, 10:00:00] Dana Demo: Manual-Demo-v9.pdf lo menciono pero no lo adjunto",
    ]
    return do_import(con, None, data="\n".join(lines).encode(), file_name="r.txt", date_order="MDY")["chat_id"]


def test_split_name():
    assert resources.split_name("Guia-Demo-Manual-v1_6.pdf") == ("guia demo manual", "Guia-Demo-Manual", (1, 6))
    assert resources.split_name("Guia-Demo-Manual-v1.4.docx")[2] == (1, 4)
    assert resources.split_name("2606.27334v1.pdf")[2] is None            # identificador, no versión
    assert resources.split_name("informe (1).pdf")[0] == resources.split_name("informe.pdf")[0]
    assert resources.split_name("Informe versión 2.pdf") == ("informe", "Informe", (2,))
    assert resources.split_name("v2.pdf") == ("v2", "v2", None)           # sin título no hay familia


def test_latest_version_is_highest_number_not_first_match(con):
    cid = chat_with_docs(con)
    lib = resources.library(con, cid, q="manual demo")
    assert lib["total"] == 1
    d = lib["items"][0]
    assert d["title"] == "Manual-Demo" and d["shares"] == 4 and d["sharers"] == ["Dana Demo", "Jota Demo"]
    assert d["latest"]["name"] == "Manual-Demo-v1_6.pdf" and d["latest"]["version"] == "1.6"
    assert [(v["version"], v["times_shared"]) for v in d["versions"]] == [("1.6", 1), ("1.5", 1), ("1.4", 2)]
    assert d["versions"][2]["ts_local"].startswith("2026-05-28") and d["versions"][2]["first_shared"].startswith("2026-05-26")
    assert d["latest_is_most_recent"] and d["has_versions"]
    # Una mención en texto no cuenta como documento compartido.
    assert all("v9" not in v["name"] for v in d["versions"])


def test_resend_of_old_version_is_flagged(con):
    data = ("[6/1/26, 9:00:00] Dana Demo: \u200eGuia-v2.pdf \u200edocument omitted\n"
            "[6/5/26, 9:00:00] Jota Demo: \u200eGuia-v1.pdf \u200edocument omitted\n").encode()
    cid = do_import(con, None, data=data, file_name="g.txt", date_order="MDY")["chat_id"]
    d = resources.library(con, cid)["items"][0]
    assert d["latest"]["version"] == "2" and d["last_shared"].startswith("2026-06-05") and not d["latest_is_most_recent"]


def test_documents_grouping_counts_and_filters(con):
    cid = chat_with_docs(con)
    lib = resources.library(con, cid)
    assert lib["counts"] == {"documents": 3, "document_shares": 7, "unnamed_documents": 0, "links": 1, "link_shares": 2}
    assert [d["title"] for d in lib["items"]] == ["informe", "2606.27334v1", "Manual-Demo"]          # más recientes primero
    assert resources.library(con, cid, order="shared")["items"][0]["title"] == "Manual-Demo"
    assert resources.library(con, cid, q="jota informe")["total"] == 1                              # también por quién compartió
    assert resources.library(con, cid, d_to="2026-06-01")["items"][0]["latest"]["version"] == "1.4"  # el período acota qué se conoce
    assert resources.library(con, cid, limit=1, offset=1)["items"][0]["title"] == "2606.27334v1"
    assert "no se puede saber" in lib["note"]


def test_link_identity():
    key, clean = resources.link_key, resources.clean_url
    same = ["https://example.org/nota", "https://example.org/nota/", "https://www.example.org/nota", "https://m.example.org/nota",
            "https://mobile.example.org/nota", "https://EXAMPLE.org/nota?utm_source=x", "https://example.org/nota/?utm_source=x&utm_medium=y"]
    assert {key(u) for u in same} == {"https://example.org/nota"}
    # utm_* se quita en cualquier posición; el resto de la consulta se conserva, en su orden y sin recodificar.
    assert key("https://example.org/a?id=3&utm_campaign=z&p=%C3%B1") == key("https://example.org/a?id=3&p=%C3%B1") == "https://example.org/a?id=3&p=%C3%B1"
    assert key("https://example.org/a?id=3") != key("https://example.org/a?id=4") != key("https://example.org/a")
    assert key("https://example.org/Nota") != key("https://example.org/nota")            # la ruta sí distingue mayúsculas
    assert key("https://example.org/a#x") != key("https://example.org/a")                # y el fragmento puede ser otra pantalla
    assert key("https://m.co/x") == "https://m.co/x" and key("https://example.org:8443/a/") == "https://example.org:8443/a"
    assert key("https://sitio.example/?utm_source=x") == key("https://sitio.example") == "https://sitio.example"
    assert key("www.example.org/nota/") == "example.org/nota" and key("http://[mal") == "http://[mal"
    # clean_url es la dirección que se muestra: la del chat, solo que sin seguimiento.
    assert clean("https://www.example.org/nota/?utm_source=x") == "https://www.example.org/nota/"
    assert clean("https://example.org/a?utm_source=x&id=3&UTM_Medium=y#parte") == "https://example.org/a?id=3#parte"
    assert clean("https://example.org/a?id=3") == "https://example.org/a?id=3" and clean("https://example.org/utm_x") == "https://example.org/utm_x"


def test_links_are_grouped_by_identity_before_counting(con):
    lines = ["[6/1/26, 10:00:00] Dana Demo: https://www.example.org/nota/?utm_source=boletin",
             "[6/2/26, 10:00:00] Jota Demo: https://example.org/nota",
             "[6/3/26, 10:00:00] Dana Demo: https://m.example.org/nota?id=7&utm_medium=social",
             "[6/4/26, 10:00:00] Jota Demo: https://example.org/nota?utm_campaign=x&id=7",
             "[6/5/26, 10:00:00] Jota Demo: https://example.org/otra"]
    cid = do_import(con, None, data="\n".join(lines).encode(), file_name="l.txt", date_order="MDY")["chat_id"]
    lib = resources.library(con, cid, kind="link", order="shared")
    assert lib["counts"]["links"] == 3 and lib["counts"]["link_shares"] == 5
    assert [(l["key"], l["url"], l["shares"]) for l in lib["items"]] == [
        ("https://example.org/nota?id=7", "https://m.example.org/nota?id=7", 2),
        ("https://example.org/nota", "https://www.example.org/nota/", 2),
        ("https://example.org/otra", "https://example.org/otra", 1)]
    assert lib["items"][1]["sharers"] == ["Dana Demo", "Jota Demo"] and lib["items"][1]["last_shared"].startswith("2026-06-02")


def test_links_grouped_by_url(con):
    cid = chat_with_docs(con)
    lib = resources.library(con, cid, kind="link")
    assert lib["total"] == 1
    l = lib["items"][0]
    assert l["domain"] == "example.org" and l["shares"] == 2            # dos mensajes, aunque uno repite la URL
    assert l["last_shared"].startswith("2026-06-24") and l["author"] == "Jota Demo" and l["sharers"] == ["Dana Demo", "Jota Demo"]
    assert resources.library(con, cid, kind="link", q="nada")["total"] == 0
