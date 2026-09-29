#!/usr/bin/env bash
set -euo pipefail

echo "install-bridge.sh has been renamed to install-remote-engine.sh." >&2
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install-remote-engine.sh" "$@"
