"""Arranque: python -m community_lens"""
import logging

import uvicorn

from .config import settings


def main() -> None:
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
