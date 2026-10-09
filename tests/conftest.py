import os
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Cada prueba usa su propia base y nunca ve credenciales reales."""
    monkeypatch.setenv("CL_DB_PATH", str(tmp_path / "test.db"))
    for k in ("TYPESAFE_API_KEY", "JEV_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("JEV_ENABLED", "0")
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    for k in [f"{p}_{v}" for p in ("ANTHROPIC", "OPENAI", "GEMINI") for v in ("API_KEY", "MODEL", "LABEL")] + ["OPENAI_BASE_URL"]:
        monkeypatch.setenv(k, "")  # vacío (no borrado): así .env no las repone
    from community_lens.config import settings
    settings.cache_clear()
    yield
    settings.cache_clear()


@pytest.fixture
def con():
    from community_lens.db import connect
    c = connect()
    yield c
    c.close()


def fixture_bytes(name: str) -> bytes:
    return (FIX / name).read_bytes()


def do_import(con, name, chat_id=None, date_order=None, tz="America/Costa_Rica", file_name=None, data=None, **kw):
    from community_lens import importer
    data = data if data is not None else fixture_bytes(name)
    pv = importer.preview(con, data, file_name or name, tz, date_order)
    assert not pv["needs_date_order"], pv["date_detection"]
    return importer.commit(con, pv["token"], file_name or name, chat_id=chat_id, chat_name=None,
                           tz_name=tz, date_order=pv["date_order"], **kw)
