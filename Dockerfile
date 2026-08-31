# Stage 1: Build Next.js static export
FROM node:20-slim AS frontend-build

ENV NEXT_TELEMETRY_DISABLED=1

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend + static frontend
FROM python:3.12-slim AS runtime

# Install uv (pinned for reproducible builds)
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    FINALLY_DB_PATH=/app/db/finally.db \
    PATH="/app/backend/.venv/bin:$PATH"

WORKDIR /app/backend

# Install Python dependencies first so the layer caches across source edits
# (README.md is required by the hatchling build backend)
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-dev

# Copy backend source
COPY backend/ ./

# Copy frontend static build output (served by FastAPI at /)
COPY --from=frontend-build /app/frontend/out ./static/

# Volume mount target for the SQLite database
RUN mkdir -p /app/db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
