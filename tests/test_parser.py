from datetime import datetime

from conftest import fixture_bytes

from community_lens import parser as P


def load(name, order):
    return P.parse(fixture_bytes(name).decode("utf-8"), order)


def by_text(parsed, needle):
    return next(r for r in parsed["records"] if needle in r.text)


def test_every_line_is_accounted_for():
    for name, order in [("ios_mdy_24h.txt", "MDY"), ("android_dmy.txt", "DMY"), ("third_party_12h.txt", "MDY")]:
        p = load(name, order)
        covered = sum(r.line_end - r.line_start + 1 for r in p["records"]) + \
            sum(i.line_end - i.line_start + 1 for i in p["issues"])
        assert covered == p["n_lines"], name


def test_ios_types_and_counts():
    p = load("ios_mdy_24h.txt", "MDY")
    d = P.diagnostics(p)
    assert d["records_detected"] == 18
    assert d["system_notices"] == 4          # cifrado, creación, 2 entradas
    assert d["unrecognized"] == 1            # aviso con U+200E que no coincide con ningún patrón
    assert d["participant_messages"] == 13
    assert d["system_notices"] + d["unrecognized"] + d["participant_messages"] == d["records_detected"]
    assert d["media_omitted_messages"] == 2 and d["deleted_messages"] == 1 and d["edited_messages"] == 1
    assert d["flagged_for_review"] == 1
    assert p["group_labels"] == ["Grupo Demo"]


def test_multiline_and_unicode_preserved():
    p = load("ios_mdy_24h.txt", "MDY")
    r = by_text(p, "Lista de ideas")
    assert r.line_start == 6 and r.line_end == 9
    assert r.text == "Lista de ideas:\n- una\n\n- dos con emoji 👩🏽‍💻 y acentos áéíóú ñ"
    assert "‍" in r.content_text  # el ZWJ del emoji no se elimina


def test_media_marker_needs_structural_evidence():
    p = load("ios_mdy_24h.txt", "MDY")
    with_caption = by_text(p, "Miren esto")
    assert with_caption.media_kind == "image" and with_caption.content_text == "Miren esto"
    typed = by_text(p, "frase que escribí yo")
    assert typed.media_kind is None and typed.record_type == "message"
    assert by_text(p, "sticker omitted").subtype == "media_only"


def test_poll_deleted_edited():
    p = load("ios_mdy_24h.txt", "MDY")
    poll = by_text(p, "POLL:")
    assert poll.is_poll and poll.record_type == "message" and "OPTION: Martes" in poll.text
    deleted = by_text(p, "This message was deleted")
    assert deleted.is_deleted and deleted.content_text == ""
    edited = by_text(p, "Me apunto")
    assert edited.is_edited and "edited" not in edited.content_text


def test_author_labels_and_kinds():
    p = load("ios_mdy_24h.txt", "MDY")
    phone = by_text(p, "Propongo")
    assert P.author_kind(phone.author_raw) == "phone"
    assert P.clean_author(phone.author_raw) == "+506 5555 0101"
    assert phone.author_raw != P.clean_author(phone.author_raw)  # el original se conserva tal cual
    assert P.author_kind(by_text(p, "curso de SQL").author_raw) == "profile_name"
    assert P.author_kind("You") == "self"


def test_links_counted_per_url():
    p = load("ios_mdy_24h.txt", "MDY")
    r = by_text(p, "Les comparto")
    assert [d for _, d in r.links] == ["example.org", "docs.example.org"]
    assert r.links[1][0] == "https://docs.example.org/guia"  # sin el punto final


def test_android_dmy_format():
    raws, _, _ = P.scan(fixture_bytes("android_dmy.txt").decode())
    det = P.detect_date_order(raws)
    assert det["status"] == "detected" and det["order"] == "DMY"
    p = load("android_dmy.txt", "DMY")
    assert p["variant"] == "dash" and p["clock"] == "24h" and not p["has_seconds"]
    assert [r.record_type for r in p["records"]] == ["system", "message", "message", "system", "message"]
    assert p["records"][1].text == "Hola a todos\nsegunda línea"
    assert p["records"][2].media_kind == "unknown"
    assert p["records"][3].subtype == "join"
    assert p["records"][4].ts == datetime(2026, 2, 1, 9, 6)


def test_12h_clock_and_third_party_notices():
    p = load("third_party_12h.txt", "MDY")
    assert p["clock"] == "12h"
    assert by_text(p, "Con texto").ts == datetime(2026, 6, 24, 12, 0, 0)       # 12 PM = mediodía
    assert by_text(p, "medianoche").ts == datetime(2026, 6, 24, 0, 30, 0)     # 12 AM = 00
    assert by_text(p, "System notification").ts.hour == 23
    assert by_text(p, "removed").record_type == "system"
    assert [i.kind for i in p["issues"]] == ["preamble"]                       # línea sin fecha: contabilizada
    ph = by_text(p, "unknown message")
    assert ph.subtype == "placeholder" and ph.needs_review


def test_ambiguous_dates_are_not_guessed():
    raws, _, _ = P.scan(fixture_bytes("ambiguous.txt").decode())
    det = P.detect_date_order(raws)
    assert det["status"] == "ambiguous" and det["order"] is None
    assert P.parse(fixture_bytes("ambiguous.txt").decode(), "MDY")["records"][0].ts.month == 3
    assert P.parse(fixture_bytes("ambiguous.txt").decode(), "DMY")["records"][0].ts.month == 4


def test_conflicting_dates_are_reported_not_dropped():
    text = fixture_bytes("conflict.txt").decode()
    raws, _, _ = P.scan(text)
    assert P.detect_date_order(raws)["status"] == "conflict"
    p = P.parse(text, "MDY")
    assert len(p["records"]) == 1 and [i.kind for i in p["issues"]] == ["invalid_date"]
    assert P.diagnostics(p)["records_detected"] == 2
