#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "Virtualenv not found. Create it and install requirements first." >&2
  exit 1
fi

cd "$ROOT"
"$PYTHON" -m uvicorn gros_pouce.server:app --app-dir backend --host 127.0.0.1 --port 47891
