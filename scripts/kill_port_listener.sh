#!/usr/bin/env bash
set -euo pipefail

port="${1:-}"

if ! [[ "$port" =~ ^[0-9]+$ ]]; then
  echo "[WARN] Invalid port: ${port}" >&2
  exit 0
fi

if command -v lsof >/dev/null 2>&1; then
  pids="$(lsof -t -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "[INFO] Force killing listener(s) on port ${port}: ${pids}"
    # User requested force-kill to recover from webhook port conflicts.
    kill -9 ${pids} 2>/dev/null || true
  fi
  exit 0
fi

if command -v fuser >/dev/null 2>&1; then
  # fuser returns non-zero when no process owns the socket.
  fuser -k -9 "${port}/tcp" >/dev/null 2>&1 || true
  exit 0
fi

echo "[WARN] Neither lsof nor fuser is available; cannot kill port ${port}" >&2
exit 0
