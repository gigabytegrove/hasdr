#!/usr/bin/with-contenv bashio
set -euo pipefail

TOKEN="$(bashio::config 'api_token')"
if [[ -z "${TOKEN}" || "${TOKEN}" == "null" ]]; then
    bashio::log.fatal "HASDR Engine API token is not configured."
    exit 1
fi

export HASDR_API_TOKEN="${TOKEN}"
export HASDR_NAME="HASDR SDR Engine"
export HASDR_LISTEN="0.0.0.0"
export HASDR_PORT="8099"
export LOG_LEVEL="INFO"

bashio::log.info "Starting HASDR SDR Engine"
exec python3 -m sdr_bridge.main
