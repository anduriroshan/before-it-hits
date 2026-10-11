#!/usr/bin/env bash
# Local dev: engine service + web app on http://localhost:8080
# Health: /api/health  (not /healthz - Cloud Run's frontend reserves that exact path)
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env.local ] && set -a && . ./.env.local && set +a
exec ./.venv/Scripts/python.exe -m uvicorn app:app --app-dir service --host 127.0.0.1 --port 8080 "$@"
