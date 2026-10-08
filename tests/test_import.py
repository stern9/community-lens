import pytest

from community_lens import importer, stats
from conftest import do_import, fixture_bytes


def count(con, sql="SELECT COUNT(*) FROM messages", *p):
    return con.execute(sql, p).fetchone()[0]


def test_ambiguous_file_requires_explicit_choice(con):
    pv = importer.preview(con, fixture_bytes("ambiguous.txt"), "ambiguous.txt", None, None)
    assert pv["needs_date_order"] and pv["diagnostics"] is None
    assert pv["timezone"] == "America/Costa_Rica"
    with pytest.raises(importer.ImportError_):
        importer.commit(con, pv["token"], "ambiguous.txt", chat_id=None, chat_name=None,
                        tz_name="America/Costa_Rica", date_order="")
    assert count(con) == 0
    r = do_import(con, "ambiguous.txt", date_order="DMY")
    assert r["status"] == "imported"
    assert con.execute("SELECT date_order, date_order_source FROM imports").fetchone()[:] == ("DMY", "user")


def test_traceability_fields(con):
    do_import(con, "ios_mdy_24h.txt")
    m = con.execute("SELECT * FROM messages WHERE text LIKE 'Lista de ideas%'").fetchone()
    assert m["uid"] and m["author_raw"] == "~ Ana Prueba" and m["ts_written"] == "2026-01-31 20:56:00"
    assert m["ts_utc"] == "2026-02-01T02:56:00Z"  # Costa Rica = UTC-6
    src = con.execute("SELECT s.*, i.file_name FROM message_sources s JOIN imports i ON i.id=s.import_id WHERE message_id=?", (m["id"],)).fetchone()
    assert (src["file_name"], src["line_start"], src["line_end"]) == ("ios_mdy_24h.txt", 6, 9)


def test_identical_reimport_is_noop(con):
    a = do_import(con, "ios_mdy_24h.txt")
    before = count(con)
    pv = importer.preview(con, fixture_bytes("ios_mdy_24h.txt"), "renombrado.txt", None, None)
    assert pv["recommendation"]["action"] == "duplicate"
    b = do_import(con, "ios_mdy_24h.txt", chat_id=a["chat_id"])
    c = do_import(con, "ios_mdy_24h.txt")  # incluso pidiendo "grupo nuevo"
    assert b["status"] == c["status"] == "duplicate"
    assert count(con) == before and count(con, "SELECT COUNT(*) FROM chats") == 1
    assert count(con, "SELECT COUNT(*) FROM imports") == 1


def test_legitimate_repeats_survive(con):
    do_import(con, "ios_mdy_24h.txt")
    rows = con.execute("SELECT uid FROM messages WHERE text='jaja'").fetchall()
    assert len(rows) == 2 and rows[0]["uid"] != rows[1]["uid"]


def test_overlapping_exports_reconcile_by_sequence(con):
    a = do_import(con, "overlap_a.txt")
    pv = importer.preview(con, fixture_bytes("overlap_b.txt"), "overlap_b.txt", None, None)
    assert pv["recommendation"]["action"] == "update" and pv["recommendation"]["chat_id"] == a["chat_id"]
    b = do_import(con, "overlap_b.txt", chat_id=a["chat_id"])
    rec = b["reconciliation"]
    # A tiene 2 "ok"; B tiene 3: dos coinciden, el tercero es un mensaje nuevo legítimo.
    assert count(con, "SELECT COUNT(*) FROM messages WHERE text='ok'") == 3
    assert rec["matched_existing"] == 3          # ok, ok, tres
    assert rec["added"] == 4                     # tercer ok, texto corregido, cuatro, cinco
    # El texto que difiere en el mismo instante NO se fusiona ni se borra: ambos quedan marcados.
    amb = con.execute("SELECT text, needs_review, review_reason FROM messages WHERE text LIKE 'texto %' ORDER BY text").fetchall()
    assert [(r["text"], r["needs_review"], r["review_reason"]) for r in amb] == [
        ("texto corregido", 1, "solape_ambiguo"), ("texto original", 1, "solape_ambiguo")]
    assert rec["ambiguous_pairs"] == 1
    # El mensaje que solo estaba en A sigue ahí, y el orden final es cronológico y sin huecos.
    seq = [r["text"] for r in con.execute("SELECT text FROM messages ORDER BY seq")]
    assert seq[0] == "uno" and seq[-2:] == ["cuatro", "cinco"] and len(seq) == 9
    assert [r[0] for r in con.execute("SELECT seq FROM messages ORDER BY seq")] == list(range(9))
    # Un mensaje presente en ambos archivos conserva las dos ubicaciones de origen.
    assert count(con, "SELECT COUNT(*) FROM message_sources s JOIN messages m ON m.id=s.message_id WHERE m.text='tres'") == 2
    # Reimportar B otra vez no cambia nada.
    assert do_import(con, "overlap_b.txt", chat_id=a["chat_id"])["status"] == "duplicate"
    assert count(con) == 9


def test_older_export_imported_second_keeps_order(con):
    b = do_import(con, "overlap_b.txt")
    do_import(con, "overlap_a.txt", chat_id=b["chat_id"])
    seq = [r["text"] for r in con.execute("SELECT text FROM messages ORDER BY seq")]
    assert seq[0] == "uno" and seq[-1] == "cinco" and seq.count("ok") == 3


def test_other_chat_is_not_merged_silently(con):
    a = do_import(con, "overlap_a.txt")
    pv = importer.preview(con, fixture_bytes("other_chat.txt"), "other_chat.txt", None, None)
    assert pv["recommendation"]["action"] == "new"
    with pytest.raises(importer.ImportError_) as e:
        do_import(con, "other_chat.txt", chat_id=a["chat_id"])
    assert e.value.code == "low_overlap"
    assert count(con) == 5 and count(con, "SELECT COUNT(*) FROM imports") == 1   # rollback completo
    r = do_import(con, "other_chat.txt")
    assert r["chat_id"] != a["chat_id"] and count(con, "SELECT COUNT(*) FROM chats") == 2


def test_similar_names_are_not_merged(con):
    data = ("[1/20/26, 10:00:00] Ana Prueba: a\n[1/20/26, 10:01:00] Ana Prueba R.: b\n"
            "[1/20/26, 10:02:00] ana prueba: c\n").encode()
    r = do_import(con, None, data=data, file_name="n.txt")
    assert stats.compute(con, r["chat_id"])["totals"]["active_authors"] == 3
    ids = [x["id"] for x in con.execute("SELECT id FROM authors ORDER BY id")]
    stats.merge_author(con, ids[1], ids[0])   # reconciliación manual explícita
    s = stats.compute(con, r["chat_id"])["totals"]
    assert s["active_authors"] == 2 and s["author_labels"] == 3
    stats.unmerge_author(con, ids[1])
    assert stats.compute(con, r["chat_id"])["totals"]["active_authors"] == 3


def test_timezone_is_editable_per_chat(con):
    r = do_import(con, "overlap_a.txt")
    importer.retime_chat(con, r["chat_id"], "Europe/Madrid", True)
    m = con.execute("SELECT ts_written, ts_utc, ts_local FROM messages ORDER BY seq").fetchone()
    assert m[:] == ("2026-05-20 10:00:00", "2026-05-20T08:00:00Z", "2026-05-20 10:00:00")
    with pytest.raises(importer.ImportError_):
        importer.retime_chat(con, r["chat_id"], "Marte/Olympus", True)


def test_delete_chat_removes_derived_data(con):
    from community_lens import classify
    a = do_import(con, "ios_mdy_24h.txt")
    b = do_import(con, "overlap_a.txt")
    classify.run(con, a["chat_id"], "rules")
    mid = con.execute("SELECT id FROM messages WHERE chat_id=? LIMIT 1", (a["chat_id"],)).fetchone()[0]
    con.execute("INSERT INTO human_labels(message_id, categories, created_at) VALUES (?, '[]', 'x')", (mid,))
    importer.delete_chat(con, a["chat_id"])
    for t in ("messages", "imports", "authors", "message_sources", "messages_fts"):
        assert count(con, f"SELECT COUNT(*) FROM {t}") > 0, t  # el otro chat sigue intacto
    assert count(con, "SELECT COUNT(*) FROM messages WHERE chat_id=?", a["chat_id"]) == 0
    for t in ("links", "classifications", "analysis_runs", "human_labels", "import_issues"):
        assert count(con, f"SELECT COUNT(*) FROM {t}") == 0, t
    assert count(con, "SELECT COUNT(*) FROM messages_fts") == count(con, "SELECT COUNT(*) FROM messages WHERE chat_id=?", b["chat_id"])
    assert count(con, "SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'curso'") == 0
