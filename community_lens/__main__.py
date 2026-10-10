"""Arranque: python -m community_lens  (o `python -m community_lens periodico …` sin pantalla; ver cli.py)"""
import logging
import sys

import uvicorn

from .config import settings

USAGE = """uso: python -m community_lens [periodico …]

  (sin argumentos)  levanta la app local (por defecto en http://127.0.0.1:8765; ver CL_HOST y CL_PORT en .env)
  periodico …       genera y publica el periódico sin pantalla; ver `python -m community_lens periodico --help`
  -h, --help        muestra esta ayuda
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
    s = settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if s.host not in ("127.0.0.1", "localhost", "::1"):
        logging.getLogger("community_lens").warning(
            "CL_HOST=%s expone la app fuera de esta máquina. No hay autenticación.", s.host)
    print(f"\n  Community Lens → http://{s.host}:{s.port}\n", flush=True)
    # access_log desactivado: las URLs de búsqueda contienen términos del chat.
    uvicorn.run("community_lens.api:app", host=s.host, port=s.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
