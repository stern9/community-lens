#!/usr/bin/env bash
# Las mismas comprobaciones que corre CI antes de aceptar un cambio. Uso: ./scripts/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/ruff ]; then
  echo "Faltan las herramientas de desarrollo. Instalalas con:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt"
  exit 1
fi

echo "== Linter (ruff)"
.venv/bin/ruff check .

if [ -d node_modules ]; then
  echo "== Frontend (esquema + compilación)"
  npm run --silent check
  if ! git diff --quiet -- web/dist web/src/api-schema.json; then
    echo "web/dist o api-schema.json cambiaron al compilar: incluilos en tu cambio."
  fi
else
  echo "== Frontend: sin node_modules, se omite (corré 'npm install' si tocaste web/src/)"
fi

echo "== Pruebas (pytest)"
.venv/bin/python -m pytest -q

echo "Todo en orden."
