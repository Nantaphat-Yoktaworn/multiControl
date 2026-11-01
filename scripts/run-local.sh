#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
mkdir -p data

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

exec .venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8765
