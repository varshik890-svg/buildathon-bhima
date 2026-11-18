#!/usr/bin/env bash
# Start the local GLiDE 0.4B decision server (run inside WSL Ubuntu).
# Env names follow the model's README: GLIDE_MODEL (required), GLIDE_DEVICE,
# GLIDE_MODEL_NAME, GLIDE_API_KEY (if set, clients must send a bearer token).
# Bhima's router talks to it at GLIDE_URL (default http://127.0.0.1:8000).
set -euo pipefail

MODEL_DIR="${GLIDE_MODEL_DIR:-$HOME/models/glide-0.4b}"
PY="${GLIDE_PY:-$HOME/venvs/glide-small/bin/python}"
PORT="${GLIDE_PORT:-8000}"

if [ ! -f "$MODEL_DIR/glide/server.py" ]; then
  echo "GLiDE files not found in $MODEL_DIR (download fastino/GLiDE-0.4B first)" >&2
  exit 1
fi

cd "$MODEL_DIR"
export GLIDE_MODEL="$MODEL_DIR"
export GLIDE_DEVICE="${GLIDE_DEVICE:-cuda}"
# One request at a time is plenty for routing; keeps answers deterministic.
export GLIDE_MAX_BATCH="${GLIDE_MAX_BATCH:-1}"

exec "$PY" -m uvicorn glide.server:app --host 127.0.0.1 --port "$PORT"
