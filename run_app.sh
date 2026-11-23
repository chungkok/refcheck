#!/usr/bin/env bash
# Start RefCheck in your browser (Linux, macOS, WSL).
# First run: creates a private Python environment in .venv with the tested versions.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements-lock.txt
fi
export REFCHECK_DEPLOYMENT=${REFCHECK_DEPLOYMENT:-local}
exec .venv/bin/python -m streamlit run app.py --server.address=127.0.0.1
