#!/usr/bin/env bash
# Останавливает процесс на порту Ops Console и запускает сервер заново.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

HOST="${HOST:-127.0.0.1}"
PORT="${APP_PORT:-9004}"
VENV_PY="${ROOT}/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
  echo "ERROR: venv not found at .venv — run: python3 -m venv .venv && .venv/bin/pip install -r ops_console/requirements.txt"
  exit 1
fi

if command -v lsof >/dev/null 2>&1; then
  pids=$(lsof -ti ":$PORT" 2>/dev/null || true)
  if [ -n "${pids:-}" ]; then
    echo "Stopping listener(s) on port $PORT: $pids"
    echo "$pids" | xargs kill 2>/dev/null || true
    sleep 0.4
  fi
fi

echo "Starting Ops Console on http://${HOST}:${PORT}"
exec "$VENV_PY" -m uvicorn ops_console.app:app --host "$HOST" --port "$PORT"
