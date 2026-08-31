#!/usr/bin/env bash
# Build (if needed) and run the FinAlly container. Idempotent: safe to re-run.
# Usage: ./scripts/start_mac.sh [--build]
set -euo pipefail

CONTAINER_NAME="finally_agents"
IMAGE_NAME="finally_agents"
VOLUME_NAME="finally-data-agents"
PORT="${PORT:-8000}"

cd "$(dirname "$0")/.."

if ! docker info >/dev/null 2>&1; then
    echo "Error: Docker does not appear to be running. Start Docker Desktop and retry." >&2
    exit 1
fi

# Build if the image is missing or --build was passed
if [[ "${1:-}" == "--build" ]] || ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    echo "Building Docker image..."
    docker build -t "$IMAGE_NAME" .
fi

# Remove any existing container with this name (running or stopped)
if [[ -n "$(docker ps -aq -f name="^${CONTAINER_NAME}$")" ]]; then
    echo "Removing existing container..."
    docker rm -f "$CONTAINER_NAME" >/dev/null
fi

# Pass .env through if present (it is never baked into the image).
# The ${a[@]+...} guard keeps an empty array safe under `set -u` on bash 3.2 (stock macOS).
ENV_FILE_ARGS=()
if [[ -f .env ]]; then
    ENV_FILE_ARGS=(--env-file .env)
else
    echo "Note: no .env found; AI chat needs OPENROUTER_API_KEY (see .env.example)."
fi

echo "Starting FinAlly..."
docker run -d \
    --name "$CONTAINER_NAME" \
    -p "${PORT}:8000" \
    -v "${VOLUME_NAME}:/app/db" \
    ${ENV_FILE_ARGS[@]+"${ENV_FILE_ARGS[@]}"} \
    "$IMAGE_NAME" >/dev/null

URL="http://localhost:${PORT}"

# Wait for the app to answer before announcing it
echo -n "Waiting for FinAlly to become healthy"
for _ in $(seq 1 30); do
    if curl -fsS -m 2 "${URL}/api/health" >/dev/null 2>&1; then
        echo " ready."
        break
    fi
    echo -n "."
    sleep 1
done
echo ""
echo "FinAlly is running at ${URL}"
echo ""

if command -v open >/dev/null 2>&1; then
    open "$URL"
fi
