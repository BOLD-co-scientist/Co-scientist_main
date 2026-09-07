#!/usr/bin/env bash
# Run the coscientist API on the HOST from this checkout (no Docker), for local
# verification of a branch without touching the running container.
#   deploy/dev_api.sh [port]        default port 8799
# Loads ./.env (ANTHROPIC_API_KEY etc.), roots state under ./state of this
# checkout (COSCIENTIST_ROOT defaults to the checkout), and never reloads.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8799}"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
export COSCIENTIST_ROOT="${COSCIENTIST_ROOT:-$PWD}"
export COSCIENTIST_PLATFORM_REPO="${COSCIENTIST_PLATFORM_REPO:-$PWD}"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m uvicorn api.server:app --host 127.0.0.1 --port "$PORT"
