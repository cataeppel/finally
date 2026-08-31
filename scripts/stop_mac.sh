#!/usr/bin/env bash
# Stop and remove the FinAlly container. The data volume is preserved.
# Idempotent: safe to re-run.
set -euo pipefail

CONTAINER_NAME="finally_agents"

if ! docker info >/dev/null 2>&1; then
    echo "Error: Docker does not appear to be running." >&2
    exit 1
fi

if [[ -n "$(docker ps -aq -f name="^${CONTAINER_NAME}$")" ]]; then
    echo "Stopping FinAlly..."
    docker rm -f "$CONTAINER_NAME" >/dev/null
    echo "FinAlly stopped. Your portfolio data is preserved in the finally-data-agents volume."
else
    echo "FinAlly is not running."
fi
