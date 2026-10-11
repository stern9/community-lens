"""Arranque: python -m community_lens  (o `python -m community_lens periodico …` sin pantalla; ver cli.py)"""
import logging
import sys

from .config import settings

USAGE = """Community Lens: analiza exportaciones de WhatsApp en tu máquina.

Levantar la app (http://127.0.0.1:8765):
  ./run.sh

Generar el periódico sin abrir la app:
  .venv/bin/python -m community_lens periodico --archivo export.zip --salida sitio/
  .venv/bin/python -m community_lens periodico --help     todas las opciones

Puerto, zona horaria y modelos se configuran en .env (ver .env.example).
"""


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["periodico"]:
        from .cli import periodico
        raise SystemExit(periodico(argv[1:]))
    if argv[:1] in (["-h"], ["--help"]):
        print(USAGE, end="")
        raise SystemExit(0)
    if argv:
        # Cualquier otra cosa es un error de tipeo: no se arranca nada ni se toca el disco.
        print(f"Argumento desconocido: {argv[0]}\n\n{USAGE}", end="", file=sys.stderr)
        raise SystemExit(2)
    try:
        import uvicorn  # acá y no arriba: la ayuda tiene que funcionar aunque falten las dependencias
    except ModuleNotFoundError:
        print("Faltan las dependencias en este Python. Arrancá con ./run.sh (crea .venv e instala) "
              "o usá .venv/bin/python -m community_lens.", file=sys.stderr)
        raise SystemExit(1) from None
    s = settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if not s.loopback:
        logging.getLogger("community_lens").warning(
            "CL_HOST=%s expone la app fuera de esta máquina. No hay autenticación.", s.host)
    print(f"\n  Community Lens → http://{s.host}:{s.port}\n", flush=True)
    # access_log desactivado: las URLs de búsqueda contienen términos del chat.
    uvicorn.run("community_lens.api:app", host=s.host, port=s.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
