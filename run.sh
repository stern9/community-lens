#!/usr/bin/env bash
# Único comando de arranque: crea el entorno si falta, instala y levanta la app.
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
# Instala la primera vez y cada vez que cambie requirements.txt (p. ej. después de un git pull).
if [ ! -f .venv/.instalado ] || [ requirements.txt -nt .venv/.instalado ]; then
  .venv/bin/pip -q install -r requirements.txt
  touch .venv/.instalado
fi
exec .venv/bin/python -m community_lens
