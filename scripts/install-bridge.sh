#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  if command -v openssl >/dev/null 2>&1; then
    TOKEN="$(openssl rand -hex 32)"
  else
    TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
  fi
  umask 077
  cat > .env <<EOF
SDR_BRIDGE_TOKEN=${TOKEN}
SDR_BRIDGE_NAME=RTL-SDR Bridge
EOF
  echo "Created .env with a random API token."
fi

docker compose up -d --build

echo
echo "RTL-SDR bridge is running on TCP/8099."
echo "Use the SDR_BRIDGE_TOKEN from $ROOT/.env when adding the Home Assistant integration."
