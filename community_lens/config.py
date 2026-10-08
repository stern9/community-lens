"""Configuración por variables de entorno (y .env local opcional)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    db_path: str
    default_tz: str
    jev_enabled: bool
    jev_api_key: str  # nunca se registra ni se devuelve por la API
    jev_model: str
    jev_threshold: float
    jev_context_messages: int
    jev_max_messages: int

    @property
    def staging_dir(self) -> Path:
        return Path(self.db_path).resolve().parent / "staging"


@lru_cache
def settings() -> Settings:
    _load_dotenv()
    e = os.environ.get
    db = e("CL_DB_PATH", "data/community_lens.db")
    if db != ":memory:" and not os.path.isabs(db):
        db = str(ROOT / db)
    return Settings(
        host=e("CL_HOST", "127.0.0.1"),
        port=int(e("CL_PORT", "8765")),
        db_path=db,
        default_tz=e("CL_DEFAULT_TZ", "America/Costa_Rica"),
        jev_enabled=e("JEV_ENABLED", "0").lower() in ("1", "true", "yes"),
        jev_api_key=e("TYPESAFE_API_KEY", ""),
        jev_model=e("JEV_MODEL", "jev-latest"),
        jev_threshold=float(e("JEV_THRESHOLD", "0.8")),
        jev_context_messages=int(e("JEV_CONTEXT_MESSAGES", "2")),
        jev_max_messages=int(e("JEV_MAX_MESSAGES", "200")),
    )
