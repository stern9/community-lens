from community_lens import report, search, stats
from conftest import do_import


def test_totals_and_denominators(con):
    cid = do_import(con, "ios_mdy_24h.txt")["chat_id"]
    s = stats.compute(con, cid)
    t = s["totals"]
    assert t["records"] == 18 and t["participant_messages"] == 13
    assert t["system_notices"] == 4 and t["unrecognized"] == 1
    assert t["participant_messages"] + t["system_notices"] + t["unrecognized"] == t["records"]
    # Los avisos no cuentan como participación: el teléfono que "entró" solo suma por su mensaje real.
    assert t["active_authors"] == 3 and t["attributed_messages"] == t["participant_messages"]
    assert t["active_days"] == 3 and t["calendar_days"] == 20      # 14 ene → 2 feb
    # "mensajes con enlaces" ≠ "cantidad de enlaces"
    assert t["messages_with_links"] == 1 and t["total_links"] == 2 and t["distinct_domains"] == 2
    assert t["media_omitted_messages"] == 2 and t["deleted_messages"] == 1
    c = s["concentration"]["top5"]
    assert c["n"] == 3 and c["denominator"] == 13 and c["messages"] == 13 and c["share"] == 1.0
    assert sum(s["weekday"]) == sum(s["hour"]) == sum(d["messages"] for d in s["daily"]) == 13
    assert "silent_members" in s["definitions"]


def test_partial_months_flagged(con):
    cid = do_import(con, "ios_mdy_24h.txt")["chat_id"]
    months = {m["month"]: m for m in stats.compute(con, cid)["monthly"]}
    assert months["2026-01"]["partial"] and months["2026-01"]["days_covered"] == 18   # desde el 14
    assert months["2026-02"]["partial"] and months["2026-02"]["days_covered"] == 2
    # Un filtro que corta el mes también lo vuelve parcial.
    m = stats.compute(con, cid, "2026-02-01", "2026-02-01")
    assert m["totals"]["participant_messages"] == 7 and m["monthly"][0]["days_covered"] == 1


def test_authors_share_uses_participant_messages(con):
    cid = do_import(con, "ios_mdy_24h.txt")["chat_id"]
    a = stats.authors(con, cid)
    assert a["denominator"] == 13 and sum(x["messages"] for x in a["authors"]) == 13
    assert abs(sum(x["share"] for x in a["authors"]) - 1) < 0.001
    assert "Grupo Demo" not in [x["label"] for x in a["authors"]]   # el "autor" de avisos no es participante


def test_fts_search_filters_and_context(con):
    cid = do_import(con, "ios_mdy_24h.txt")["chat_id"]
    r = search.search(con, q="articulo", chat_id=cid)                 # sin tilde encuentra "artículo"
    assert r["total"] == 1 and search.SNIP_A in r["items"][0]["snippet"]
    m = r["items"][0]
    assert m["chat_name"] and m["author_label"] == "Beto Ficticio" and m["ts_local"] == "2026-01-31 20:55:10"
    assert m["ref"].startswith("CL-") and m["sources"][0]["line_start"] == 5
    assert search.search(con, q="jaja", chat_id=cid)["total"] == 2
    assert search.search(con, q="jaja", chat_id=cid, d_to="2026-01-31")["total"] == 0
    assert search.search(con, q='"buen curso"', chat_id=cid)["total"] == 1
    assert search.search(con, q="prop*", chat_id=cid)["total"] == 1
    ana = con.execute("SELECT id FROM authors WHERE display_label='Ana Prueba'").fetchone()[0]
    assert search.search(con, chat_id=cid, author_id=ana)["total"] == 7
    assert search.search(con, chat_id=cid, has_link=True)["total"] == 1
    assert search.search(con, chat_id=cid, record_type="system")["total"] == 4
    assert search.search(con, chat_id=cid, record_type="all", review_only=True)["total"] == 1
    # Sintaxis FTS hostil no rompe la consulta
    for q in ['"', 'a AND (', 'NEAR(', '*', 'col:x', "'; DROP TABLE messages; --"]:
        search.search(con, q=q, chat_id=cid)
    ctx = search.context(con, m["id"], 1, 1)
    assert [i["id"] == m["id"] for i in ctx["items"]] == [False, True, False]
    assert "curso de SQL" in ctx["items"][0]["text"] and "Lista de ideas" in ctx["items"][2]["text"]


def test_report_is_aggregate_only(con):
    cid = do_import(con, "ios_mdy_24h.txt")["chat_id"]
    r = report.build(con, cid)
    html, txt = report.to_html(r), report.to_text(r)
    for out in (html, txt):
        assert "Mensajes de participantes" in out and "example.org" in out
        for private in ("curso de SQL", "Beto Ficticio", "Ana Prueba", "5555", "<script>alert"):
            assert private not in out
    assert "mes parcial" in txt and "No se ejecutó ninguna clasificación" in txt
    assert "ene-26" in txt and "feb-26" in html and ">26-01" not in html
    assert report.month_label("2026-09") == "sep-26"
    with_authors = report.to_text(report.build(con, cid, include_authors=True))
    assert "Beto Ficticio" in with_authors and "curso de SQL" not in with_authors


def test_report_escapes_chat_name(con):
    cid = do_import(con, "ios_mdy_24h.txt")["chat_id"]
    con.execute("UPDATE chats SET name=? WHERE id=?", ('<img src=x onerror=alert(1)>', cid))
    html = report.to_html(report.build(con, cid))
    assert "<img src=x" not in html and "&lt;img src=x" in html


def test_concentration_curve(con):
    cid = do_import(con, "ios_mdy_24h.txt")["chat_id"]
    c = stats.compute(con, cid)["concentration"]
    # Ana 7, Beto 5, teléfono 1 → acumulado 7, 12, 13 sobre 13
    assert [p["messages"] for p in c["curve"]] == [7, 12, 13] and c["denominator"] == 13
    assert c["curve"][-1]["share"] == 1.0 and c["half_rank"] == 1
    assert c["curve"][0]["share"] == round(7 / 13, 4) and c["top5"]["share"] == c["curve"][-1]["share"]
    assert stats.compute(con, cid, "2030-01-01")["concentration"]["curve"] == []


def test_conversation_days_start_at_first_participant_message(con):
    cid = do_import(con, "ios_mdy_24h.txt")["chat_id"]
    s = stats.compute(con, cid)
    # El grupo se crea el 14 ene (solo avisos); el primer mensaje de participante es del 31 ene.
    assert s["totals"]["calendar_days"] == 20
    assert s["conversation"]["first_day"] == "2026-01-31" and s["conversation"]["last_day"] == "2026-02-02"
    assert s["totals"]["conversation_days"] == 3 and s["totals"]["silent_days"] == 0


def test_silent_days_listed_and_add_up(con):
    data = ("[3/2/26, 10:00:00] Ana Prueba: lunes\n[3/3/26, 10:00:00] Beto Ficticio: \u200eBeto Ficticio left\n"
            "[3/5/26, 10:00:00] Ana Prueba: jueves\n[3/8/26, 10:00:00] Ana Prueba: domingo\n").encode()
    cid = do_import(con, None, data=data, file_name="s.txt", date_order="MDY")["chat_id"]
    s = stats.compute(con, cid)
    t = s["totals"]
    assert (t["active_days"], t["conversation_days"], t["silent_days"]) == (3, 7, 4)
    assert t["active_days"] + t["silent_days"] == t["conversation_days"]
    # El 3 de marzo solo tiene un aviso del sistema: sigue siendo un día sin mensajes.
    assert [(d["day"], d["weekday"]) for d in s["conversation"]["silent_days"]] == [
        ("2026-03-03", 2), ("2026-03-04", 3), ("2026-03-06", 5), ("2026-03-07", 6)]
    # Con filtro, el tramo se recorta al período pero sigue acotado por el primer/último mensaje del chat.
    f = stats.compute(con, cid, "2026-03-06", "2026-03-31")
    assert f["conversation"]["first_day"] == "2026-03-06" and f["conversation"]["last_day"] == "2026-03-08"
    assert (f["totals"]["active_days"], f["totals"]["silent_days"], f["totals"]["conversation_days"]) == (1, 2, 3)
    empty = stats.compute(con, cid, "2027-01-01")
    assert empty["totals"]["conversation_days"] == 0 and empty["conversation"]["silent_days"] == []
    txt = report.to_text(report.build(con, cid))
    assert "DÍAS SIN MENSAJES (4 de 7" in txt and "mar 2026-03-03" in txt


def test_averages_state_their_denominators(con):
    data = "".join(f"[{mo}/{d}/26, {h}:00:00] Ana Prueba: m{mo}{d}{h}\n" for mo, d, h in
                   [(1, 30, 9), (1, 31, 9), (2, 1, 9), (2, 1, 9), (2, 14, 10), (2, 28, 9), (3, 1, 9), (3, 2, 23)]).encode()
    cid = do_import(con, None, data=data, file_name="a.txt", date_order="MDY")["chat_id"]
    a = stats.compute(con, cid)["averages"]
    # 8 mensajes entre el 30 ene y el 2 mar = 32 días de conversación
    assert a["per_day_denominator"] == 32 and a["per_day"] == round(8 / 32, 1)
    assert a["per_hour_denominator"] == 32 * 24 and a["per_hour"] == round(8 / (32 * 24), 2)
    # Solo febrero está completo (4 mensajes): enero y marzo son parciales y no entran.
    assert (a["per_month_denominator"], a["per_month_messages"], a["per_month"]) == (1, 4, 4.0)
    assert a["peak_hour"] == 9 and a["peak_hour_messages"] == 6 and a["peak_hour_per_day"] == round(6 / 32, 1)
    assert a["per_active_day"] == round(8 / 7, 1)
    none = stats.compute(con, cid, "2026-03-01")["averages"]
    assert none["per_month"] is None and none["per_month_denominator"] == 0 and none["per_day"] == 1.0
    empty = stats.compute(con, cid, "2027-01-01")["averages"]
    assert empty["per_day"] is None and empty["peak_hour"] is None
    txt = report.to_text(report.build(con, cid))
    assert "Promedio de mensajes por mes: 4,0 (4 mensajes en 1 meses completos" in txt and "hora pico es 09:00" in txt
    assert "no hay meses completos" in report.to_text(report.build(con, cid, "2026-03-01"))
    assert "sin mensajes en el período" in report.to_html(report.build(con, cid, "2027-01-01"))
