import json

import pytest

from community_lens import classify, search
from community_lens.classify import jev
from community_lens.classify.base import ContextMessage, Item, ProviderError
from community_lens.classify.rules import RulesProvider
from community_lens.classify.taxonomy import CATEGORY_KEYS, TAXONOMY_VERSION
from community_lens.config import settings
from conftest import do_import


def cats(text, **kw):
    return RulesProvider().classify_one(Item(1, text, **kw)).assigned


def test_rules_basic_and_multilabel():
    assert cats("¿Alguien sabe de un buen curso?") == ["pregunta"]
    assert cats("jaja qué bueno") == ["conversacion_general"]
    assert cats("mirá https://example.org/x?id=3", has_link=True) == ["recurso_compartido"]   # "?" de la URL no es pregunta
    assert set(cats("Propongo que hagamos un meetup, ¿quién se apunta?")) == {"pregunta", "propuesta_de_proyecto"}
    assert cats("Les recuerdo que mañana a las 6 es el taller, cupos limitados") == ["anuncio"]
    assert cats("Creo que puso el link del hackathon en ambos") == ["conversacion_general"]   # mencionar un evento no es anunciarlo
    assert cats("Me apunto, puedo ayudar con la logística") == ["oferta_de_colaboracion"]


def test_rules_use_bounded_context():
    q = ContextMessage("¿Alguien puede revisar mi propuesta?", same_author=False, is_question=True)
    assert cats("Yo, con gusto", context=[q]) == ["oferta_de_colaboracion"]
    assert cats("Yo, con gusto") == ["conversacion_general"]
    own = ContextMessage("¿Alguien puede?", same_author=True, is_question=True)
    assert cats("Yo creo que sí", context=[own]) == ["conversacion_general"]


def test_rules_run_is_recorded_and_app_works_without_jev(con):
    cid = do_import(con, "ios_mdy_24h.txt")["chat_id"]
    info = {p["name"]: p for p in classify.providers_info()}
    assert info["rules"]["available"] and not info["jev"]["available"] and not info["openai_decisions"]["available"]
    r = classify.run(con, cid, "rules")
    run = con.execute("SELECT * FROM analysis_runs WHERE id=?", (r["run_id"],)).fetchone()
    assert (run["provider"], run["model"], run["taxonomy_version"], run["status"]) == ("rules", "rules-1", TAXONOMY_VERSION, "completed")
    assert run["started_at"] and run["finished_at"] and json.loads(run["config"])["scores"] is False
    assert r["classified"] == 11            # 13 mensajes − 2 sin texto (sticker solo, eliminado)
    assert con.execute("SELECT COUNT(*) FROM classifications WHERE score IS NOT NULL").fetchone()[0] == 0
    s = search.category_summary(con, cid)
    assert s["classified_messages"] == 11 and s["participant_messages"] == 13
    assert s["auto"]["pregunta"] == 2 and s["auto"]["propuesta_de_proyecto"] == 1 and s["auto"]["oferta_de_colaboracion"] == 1
    assert search.search(con, chat_id=cid, category="oferta_de_colaboracion")["items"][0]["auto"][0]["provider"] == "rules"


def test_human_correction_never_overwrites_prediction(con):
    cid = do_import(con, "ios_mdy_24h.txt")["chat_id"]
    classify.run(con, cid, "rules")
    m = search.search(con, q="jaja", chat_id=cid)["items"][0]
    assert m["effective"] == ["conversacion_general"] and m["effective_source"] == "auto"
    before = con.execute("SELECT id, category, assigned FROM classifications WHERE message_id=?", (m["id"],)).fetchall()
    n_anuncio = search.search(con, chat_id=cid, category="anuncio")["total"]
    con.execute("INSERT INTO human_labels(message_id, categories, created_at) VALUES (?,?, 't1')", (m["id"], '["anuncio"]'))
    after = con.execute("SELECT id, category, assigned FROM classifications WHERE message_id=?", (m["id"],)).fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    got = next(i for i in search.search(con, q="jaja", chat_id=cid)["items"] if i["id"] == m["id"])
    assert got["effective"] == ["anuncio"] and got["effective_source"] == "human"
    assert [a["category"] for a in got["auto"]] == ["conversacion_general"]     # la predicción sigue visible
    assert search.search(con, chat_id=cid, category="anuncio")["total"] == n_anuncio + 1
    assert search.search(con, chat_id=cid, category="conversacion_general", q="jaja")["total"] == 1
    s = search.category_summary(con, cid)
    assert s["corrected_messages"] == 1 and s["human"]["anuncio"] == 1 and s["effective"]["anuncio"] == s["auto"]["anuncio"] + 1
    # Revertir agrega otra fila (historial), no borra.
    con.execute("INSERT INTO human_labels(message_id, categories, created_at) VALUES (?, NULL, 't2')", (m["id"],))
    assert search.search(con, chat_id=cid, category="anuncio")["total"] == n_anuncio
    assert con.execute("SELECT COUNT(*) FROM human_labels").fetchone()[0] == 2


def test_jev_blocked_without_credentials_or_consent(con, monkeypatch):
    cid = do_import(con, "ios_mdy_24h.txt")["chat_id"]
    with pytest.raises(ProviderError):
        classify.run(con, cid, "jev", consent=True)                       # desactivado
    monkeypatch.setenv("JEV_ENABLED", "1"); settings.cache_clear()
    with pytest.raises(ProviderError, match="TYPESAFE_API_KEY"):
        classify.run(con, cid, "jev", consent=True)                       # sin clave
    monkeypatch.setenv("TYPESAFE_API_KEY", "clave-de-prueba"); settings.cache_clear()
    with pytest.raises(ProviderError, match="confirmación"):
        classify.run(con, cid, "jev", consent=False)                      # sin consentimiento
    assert con.execute("SELECT COUNT(*) FROM analysis_runs WHERE provider='jev' AND status<>'failed'").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM classifications").fetchone()[0] == 0
    assert "clave-de-prueba" not in json.dumps(classify.providers_info())


def test_jev_request_shape_matches_documented_contract():
    it = Item(7, "¿Alguien sabe?", context=[ContextMessage("previo", False)])
    body = jev.build_request(it, "jev-latest")
    assert set(body) == {"model", "state", "questions"} and body["model"] == "jev-latest"
    assert body["state"] == {"message": "¿Alguien sabe?", "previous_messages": ["previo"]}
    assert list(body["questions"]) == CATEGORY_KEYS
    assert all(set(q) == {"type", "instructions"} and q["type"] == "noul" for q in body["questions"].values())


def test_jev_adapter_with_stub_transport_stores_scores_and_rejects_bad_shapes(con, monkeypatch):
    """Usa un transporte falso SOLO para probar el adaptador; no es un resultado de Jev."""
    monkeypatch.setenv("JEV_ENABLED", "1"); monkeypatch.setenv("TYPESAFE_API_KEY", "k"); settings.cache_clear()
    cid = do_import(con, "overlap_a.txt")["chat_id"]
    sent = []

    def transport(payload):
        sent.append(payload)
        return {"model": "jev-1.13.0", "usage": {"input_tokens": 1, "output_tokens": 0},
                "answers": {k: {"type": "noul", "noul": 0.93 if k == "pregunta" else 0.1} for k in CATEGORY_KEYS}}

    r = classify.run(con, cid, "jev", consent=True, provider=jev.JevProvider(transport))
    assert r["status"] == "completed" and r["classified"] == 5 and r["model"] == "jev-1.13.0"
    assert all("Ensayo" not in json.dumps(p, ensure_ascii=False) for p in sent)        # no viajan los autores
    rows = con.execute("SELECT category, assigned, score FROM classifications WHERE message_id=(SELECT MIN(id) FROM messages)").fetchall()
    assert len(rows) == 6 and {(x["category"], x["assigned"], x["score"]) for x in rows} >= {("pregunta", 1, 0.93), ("anuncio", 0, 0.1)}
    cfg = json.loads(con.execute("SELECT config FROM analysis_runs").fetchone()[0])
    assert cfg["threshold"] == 0.8 and cfg["score_kind"] == "noul" and "k" not in cfg.values()

    bad = jev.JevProvider(lambda p: {"answers": {"pregunta": {"type": "noul"}}})
    with pytest.raises(ProviderError):
        classify.run(con, cid, "jev", consent=True, provider=bad)
    assert con.execute("SELECT status FROM analysis_runs ORDER BY id DESC").fetchone()[0] == "failed"
    for body in ({}, {"answers": []}, {"answers": {k: {"noul": 1.5} for k in CATEGORY_KEYS}}, {"answers": {k: {"noul": True} for k in CATEGORY_KEYS}}):
        with pytest.raises(ProviderError):
            jev.parse_response(body)


def test_category_monthly_uses_effective_labels_and_classified_denominator(con):
    cid = do_import(con, "ios_mdy_24h.txt")["chat_id"]
    assert search.category_monthly(con, cid) == {"run": None, "months": []}
    classify.run(con, cid, "rules")
    res = search.category_monthly(con, cid)
    by = {m["month"]: m for m in res["months"]}
    assert list(by) == ["2026-01", "2026-02"] and res["run"]["provider"] == "rules"
    jan, feb = by["2026-01"], by["2026-02"]
    # Enero: 5 mensajes, 4 con texto (el sticker no se clasifica)
    assert (jan["participant_messages"], jan["classified"]) == (5, 4)
    assert jan["counts"]["pregunta"] == 1 and jan["shares"]["pregunta"] == 0.25
    assert (feb["participant_messages"], feb["classified"]) == (8, 7)
    total = search.category_summary(con, cid)
    for k in CATEGORY_KEYS:  # la suma mensual coincide con el resumen del período
        assert jan["counts"][k] + feb["counts"][k] == total["effective"][k], k
    # Una corrección humana mueve el conteo del mes sin tocar la predicción.
    m = search.search(con, q="jaja", chat_id=cid)["items"][0]
    before = feb["counts"]["conversacion_general"]
    con.execute("INSERT INTO human_labels(message_id, categories, created_at) VALUES (?,?, 't')", (m["id"], '["anuncio"]'))
    feb2 = {x["month"]: x for x in search.category_monthly(con, cid)["months"]}["2026-02"]
    assert feb2["counts"]["conversacion_general"] == before - 1 and feb2["counts"]["anuncio"] == feb["counts"]["anuncio"] + 1
    assert feb2["classified"] == 7


def test_partial_run_does_not_replace_fuller_active_run(con, monkeypatch):
    """Una corrida de Jev topada no debe tapar la clasificación completa vigente."""
    cid = do_import(con, "ios_mdy_24h.txt")["chat_id"]
    full = classify.run(con, cid, "rules")
    assert full["activated"] and search.latest_run(con, cid) == full["run_id"]
    monkeypatch.setenv("JEV_ENABLED", "1"); monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("JEV_MAX_MESSAGES", "3"); settings.cache_clear()
    stub = jev.JevProvider(lambda p: {"model": "jev-x", "answers": {k: {"type": "noul", "noul": 0.9 if k == "anuncio" else 0.0} for k in CATEGORY_KEYS}})
    part = classify.run(con, cid, "jev", consent=True, provider=stub)
    assert part["classified"] == 3 and part["truncated"] and not part["activated"] and part["active_run_id"] == full["run_id"]
    # La vista sigue mostrando la ejecución completa...
    s = search.category_summary(con, cid)
    assert s["run"]["id"] == full["run_id"] and s["classified_messages"] == 11
    assert search.search(con, chat_id=cid, category="pregunta")["total"] == 2
    # ...la parcial quedó guardada y se puede activar a mano, y volver atrás.
    assert con.execute("SELECT COUNT(*) FROM classifications WHERE run_id=?", (part["run_id"],)).fetchone()[0] == 18
    search.set_active_run(con, cid, part["run_id"])
    assert search.category_summary(con, cid)["classified_messages"] == 3
    assert search.search(con, chat_id=cid, category="anuncio")["total"] == 3
    search.set_active_run(con, cid, full["run_id"])
    assert search.category_summary(con, cid)["auto"]["pregunta"] == 2
    # Una ejecución nueva que cubre lo mismo o más sí pasa a ser la vigente.
    again = classify.run(con, cid, "rules")
    assert again["activated"] and search.latest_run(con, cid) == again["run_id"]
    with pytest.raises(KeyError):
        search.set_active_run(con, cid, 9999)
