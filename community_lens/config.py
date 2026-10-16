"""Configuración por variables de entorno (y .env local opcional).

Settings es la única fuente: ningún otro módulo lee el entorno por su cuenta.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Las pruebas lo apagan (tests/conftest.py) para no depender del .env de quien las corre.
LOAD_DOTENV = True

# Campo de Settings -> (variable de entorno, valor por defecto). Es la lista completa de lo que la app lee del
# entorno; las pruebas la usan para arrancar sin ninguna de estas variables.
ENV: dict[str, tuple[str, str]] = {
    "host": ("CL_HOST", "127.0.0.1"),
    "port": ("CL_PORT", "8765"),
    "allowed_hosts": ("CL_ALLOWED_HOSTS", ""),
    "db_path": ("CL_DB_PATH", "data/community_lens.db"),
    "default_tz": ("CL_DEFAULT_TZ", "America/Costa_Rica"),
    "jev_enabled": ("JEV_ENABLED", "0"),
    "jev_api_key": ("TYPESAFE_API_KEY", ""),
    "jev_model": ("JEV_MODEL", "jev-latest"),
    "jev_threshold": ("JEV_THRESHOLD", "0.8"),
    "jev_context_messages": ("JEV_CONTEXT_MESSAGES", "2"),
    "jev_max_messages": ("JEV_MAX_MESSAGES", "200"),
    "anthropic_api_key": ("ANTHROPIC_API_KEY", ""),
    "anthropic_model": ("ANTHROPIC_MODEL", ""),
    "anthropic_label": ("ANTHROPIC_LABEL", ""),
    "openai_api_key": ("OPENAI_API_KEY", ""),
    "openai_model": ("OPENAI_MODEL", ""),
    "openai_label": ("OPENAI_LABEL", ""),
    "openai_base_url": ("OPENAI_BASE_URL", ""),
    "gemini_api_key": ("GEMINI_API_KEY", ""),
    "gemini_model": ("GEMINI_MODEL", ""),
    "gemini_label": ("GEMINI_LABEL", ""),
    "newspaper_name": ("CL_PERIODICO_NOMBRE", ""),
    "newspaper_subtitle": ("CL_PERIODICO_SUBTITULO", ""),
}
ENV_VARS = tuple(var for var, _ in ENV.values())
# Valores de CL_HOST con los que la app solo se alcanza desde esta máquina.
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _load_dotenv() -> None:
    f = ROOT / ".env"
    if not LOAD_DOTENV or not f.exists():
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
    allowed_hosts: str  # nombres extra con los que se puede abrir la app, separados por coma (ver api.py)
    db_path: str
    default_tz: str
    jev_enabled: bool
    jev_api_key: str = field(repr=False)  # las keys nunca se registran ni se devuelven por la API
    jev_model: str
    jev_threshold: float
    jev_context_messages: int
    jev_max_messages: int
    # Modelos del periódico (ver llm.py). Vacío = sin configurar.
    anthropic_api_key: str = field(repr=False)
    anthropic_model: str
    anthropic_label: str
    openai_api_key: str = field(repr=False)
    openai_model: str
    openai_label: str
    openai_base_url: str
    gemini_api_key: str = field(repr=False)
    gemini_model: str
    gemini_label: str
    # Nombre público del periódico (p. ej. el de la comunidad); nunca el nombre del grupo de WhatsApp.
    newspaper_name: str
    newspaper_subtitle: str

    @property
    def loopback(self) -> bool:
        return self.host in LOOPBACK

    @property
    def staging_dir(self) -> Path:
        return Path(self.db_path).resolve().parent / "staging"


@lru_cache
def settings() -> Settings:
    _load_dotenv()
    v = {name: os.environ.get(var, default).strip() for name, (var, default) in ENV.items()}
    if v["db_path"] != ":memory:" and not os.path.isabs(v["db_path"]):
        v["db_path"] = str(ROOT / v["db_path"])
    return Settings(**{
        **v,
        "port": int(v["port"]),
        "jev_enabled": v["jev_enabled"].lower() in ("1", "true", "yes"),
        "jev_threshold": float(v["jev_threshold"]),
        "jev_context_messages": int(v["jev_context_messages"]),
        "jev_max_messages": int(v["jev_max_messages"]),
    })
