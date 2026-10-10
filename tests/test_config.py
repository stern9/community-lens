"""La configuración sale de un solo lugar, y las pruebas no dependen del .env ni de la terminal de quien las corre."""
import dataclasses

from community_lens import config, llm

DOTENV = """# .env de alguien con otra configuración
CL_DEFAULT_TZ=Europe/Madrid
CL_PORT=9999
JEV_THRESHOLD=0.1
JEV_ENABLED=1
TYPESAFE_API_KEY="clave-real"
ANTHROPIC_API_KEY=clave-real
"""


def test_dotenv_is_not_read_while_testing(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(DOTENV, encoding="utf-8")
    monkeypatch.setattr(config, "ROOT", tmp_path)       # ahí buscaría la app su .env
    monkeypatch.setattr(config.os, "environ", dict(config.os.environ))
    assert config.LOAD_DOTENV is False
    config.settings.cache_clear()
    s = config.settings()
    assert (s.default_tz, s.port, s.jev_threshold, s.jev_enabled, s.jev_api_key) == ("America/Costa_Rica", 8765, 0.8, False, "")
    assert llm.providers() == [] and "clave-real" not in str(config.os.environ)
    # El mismo archivo sí se lee cuando la carga está encendida (como al arrancar la app): la prueba no es vacía.
    monkeypatch.setattr(config, "LOAD_DOTENV", True)
    config.settings.cache_clear()
    s = config.settings()
    assert (s.default_tz, s.port, s.jev_threshold, s.jev_enabled, s.jev_api_key) == ("Europe/Madrid", 9999, 0.1, True, "clave-real")
    assert [p.name for p in llm.providers()] == ["anthropic"] and "clave-real" not in repr(s)


def test_settings_describe_everything_the_app_reads(monkeypatch):
    assert set(config.ENV) == {f.name for f in dataclasses.fields(config.Settings)}
    # Lo exportado en la terminal tampoco llega a las pruebas: conftest borra cada variable de la lista.
    assert not [v for v in config.ENV_VARS if v != "CL_DB_PATH" and v in config.os.environ]
    example = (config.ROOT / ".env.example").read_text(encoding="utf-8")
    assert all(f"\n{var}=" in example for var in config.ENV_VARS)   # y .env.example las documenta todas
