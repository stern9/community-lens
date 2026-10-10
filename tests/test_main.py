"""Arranque por línea de comandos: la ayuda y los argumentos desconocidos no levantan nada."""
import pytest
import uvicorn

from community_lens import __main__ as entry
from community_lens import importer


@pytest.fixture
def nothing_starts(monkeypatch):
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("no debe arrancar el servidor"))
    monkeypatch.setattr(importer, "clear_staging", lambda *a, **k: pytest.fail("no debe tocar data/staging"))


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_prints_usage_and_starts_nothing(nothing_starts, capsys, flag):
    with pytest.raises(SystemExit) as e:
        entry.main([flag])
    out = capsys.readouterr()
    assert e.value.code == 0 and "periodico" in out.out and out.err == ""


@pytest.mark.parametrize("argv", [["periodcio"], ["--ayuda"], ["serve", "--port", "1"]])
def test_unknown_argument_fails_without_touching_disk(nothing_starts, capsys, argv):
    with pytest.raises(SystemExit) as e:
        entry.main(argv)
    out = capsys.readouterr()
    assert e.value.code == 2 and "periodico" in out.err and argv[0] in out.err and out.out == ""


def test_no_arguments_still_starts_the_server(monkeypatch, capsys):
    started = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.append((app, kw)))
    entry.main([])
    assert started[0][0] == "community_lens.api:app" and started[0][1]["access_log"] is False
    assert "http://127.0.0.1:8765" in capsys.readouterr().out


def test_missing_dependencies_give_a_hint_instead_of_a_traceback(monkeypatch, capsys):
    """Con un Python que no es el de .venv: la ayuda igual sale y arrancar explica qué hacer."""
    monkeypatch.setitem(__import__("sys").modules, "uvicorn", None)  # importarlo lanza ModuleNotFoundError
    with pytest.raises(SystemExit) as e:
        entry.main(["--help"])
    assert e.value.code == 0 and "periodico" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        entry.main([])
    assert e.value.code == 1 and "./run.sh" in capsys.readouterr().err
