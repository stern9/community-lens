#!/usr/bin/env bash
# Único comando de arranque: crea el entorno si falta, instala y levanta la app.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip -q install -r requirements.txt
fi
exec .venv/bin/python -m community_lens
