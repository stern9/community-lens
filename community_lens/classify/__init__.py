"""Clasificación opcional: registro de proveedores y ejecución de análisis."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..config import settings
from .base import ContextMessage, Item, Provider, ProviderError, Result  # noqa: F401
from .jev import JevProvider
from .openai_decisions import OpenAIDecisionsProvider
from .rules import RulesProvider, looks_like_question
from .taxonomy import CATEGORY_KEYS, TAXONOMY_VERSION

PROVIDERS: dict[str, type[Provider]] = {
    "rules": RulesProvider,
    "jev": JevProvider,
    "openai_decisions": OpenAIDecisionsProvider,
}
CONTEXT_WINDOW_MINUTES = 15


def providers_info() -> list[dict]:
    return [cls().info() for cls in PROVIDERS.values()]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _items(con, chat_id: int, d_from, d_to, n_context: int, limit: int | None) -> tuple[list[Item], int]:
    rows = con.execute(
        """SELECT id, seq, author_id, ts_utc, content_text, link_count, media_kind, is_poll, substr(ts_local,1,10) d
           FROM messages WHERE chat_id=? AND record_type='message' ORDER BY seq""", (chat_id,)).fetchall()
    items = []
    eligible = 0
    for i, r in enumerate(rows):
        if not r["content_text"] or (d_from and r["d"] < d_from) or (d_to and r["d"] > d_to):
            continue
        eligible += 1
        if limit is not None and len(items) >= limit:
            continue
        ctx = []
        t = datetime.strptime(r["ts_utc"], "%Y-%m-%dT%H:%M:%SZ")
        for p in rows[max(0, i - n_context):i]:
            if not p["content_text"]:
                continue
            if (t - datetime.strptime(p["ts_utc"], "%Y-%m-%dT%H:%M:%SZ")).total_seconds() > CONTEXT_WINDOW_MINUTES * 60:
                continue
            ctx.append(ContextMessage(p["content_text"], p["author_id"] == r["author_id"],
                                      looks_like_question(p["content_text"])))
        items.append(Item(r["id"], r["content_text"], r["link_count"] > 0, r["media_kind"], bool(r["is_poll"]), ctx))
    return items, eligible


def run(con, chat_id: int, provider_name: str, d_from: str | None = None, d_to: str | None = None,
        consent: bool = False, provider: Provider | None = None) -> dict:
    if provider is None:
        if provider_name not in PROVIDERS:
            raise ProviderError("Proveedor desconocido.")
        provider = PROVIDERS[provider_name]()
    info = provider.info()
    if not info["available"]:
        raise ProviderError(info["reason"] or "Proveedor no disponible.")
    if provider.external and not consent:
        raise ProviderError("Este proveedor envía texto fuera de esta máquina y requiere confirmación explícita.")
    s = settings()
    n_ctx = s.jev_context_messages if provider.external else 2
    limit = s.jev_max_messages if provider.external else None
    items, eligible = _items(con, chat_id, d_from, d_to, n_ctx, limit)
    cfg = {**provider.config(), "eligible_messages": eligible, "context_window_minutes": CONTEXT_WINDOW_MINUTES}
    run_id = con.execute(
        """INSERT INTO analysis_runs(chat_id, provider, model, taxonomy_version, config, date_from, date_to,
             started_at, status) VALUES (?,?,?,?,?,?,?,?, 'running')""",
        (chat_id, provider.name, provider.model(), TAXONOMY_VERSION, json.dumps(cfg, ensure_ascii=False),
         d_from, d_to, _now())).lastrowid
    try:
        results, errors = provider.classify(items)
    except ProviderError as e:
        con.execute("UPDATE analysis_runs SET status='failed', finished_at=?, error=? WHERE id=?", (_now(), str(e), run_id))
        raise
    con.execute("BEGIN IMMEDIATE")
    for r in results:
        ev = json.dumps(r.evidence, ensure_ascii=False) if r.evidence else None
        cats = CATEGORY_KEYS if r.scores is not None else r.assigned
        for c in cats:
            con.execute("INSERT INTO classifications(run_id, message_id, category, assigned, score, evidence) VALUES (?,?,?,?,?,?)",
                        (run_id, r.message_id, c, int(c in r.assigned), (r.scores or {}).get(c), ev))
    status = "completed" if not errors else "partial"
    con.execute("UPDATE analysis_runs SET status=?, finished_at=?, n_messages=?, n_errors=?, model=?, error=? WHERE id=?",
                (status, _now(), len(results), errors, provider.model(), getattr(provider, "last_error", None), run_id))
    con.execute("COMMIT")
    # Una ejecución nueva pasa a ser la vigente solo si cubre al menos tantos mensajes como la
    # vigente actual. Así una corrida parcial (tope de Jev, período acotado) no tapa una completa.
    cur = con.execute(
        """SELECT r.id, r.n_messages FROM chats c JOIN analysis_runs r ON r.id = c.active_run_id
           WHERE c.id = ? AND r.status IN ('completed','partial')""", (chat_id,)).fetchone()
    if cur is None:
        cur = con.execute("""SELECT id, n_messages FROM analysis_runs WHERE chat_id=? AND id<>? AND status IN
                             ('completed','partial') ORDER BY id DESC LIMIT 1""", (chat_id, run_id)).fetchone()
    activated = cur is None or len(results) >= cur["n_messages"]
    con.execute("UPDATE chats SET active_run_id=? WHERE id=?", (run_id if activated else cur["id"], chat_id))
    return {"run_id": run_id, "status": status, "provider": provider.name, "model": provider.model(),
            "classified": len(results), "errors": errors, "eligible": eligible,
            "truncated": limit is not None and eligible > len(items),
            "activated": activated, "active_run_id": run_id if activated else cur["id"]}
