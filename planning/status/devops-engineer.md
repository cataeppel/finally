# devops-engineer — updated 2026-08-31T15:55:00Z
State: done
Done:
- Audited and hardened `Dockerfile`: multi-stage (node:20-slim `frontend-build` -> python:3.12-slim `runtime`),
  uv pinned to 0.12.7 (was `:latest`), stage 2 named, `HEALTHCHECK` on `/api/health`,
  `FINALLY_DB_PATH=/app/db/finally.db` set explicitly, venv on `PATH` so CMD is plain
  `uvicorn` (no `uv run` resolution at container start).
- Rewrote `.dockerignore`: `.env` and `.env.*` excluded (verified no `.env` anywhere in the image),
  recursive `__pycache__`, `planning/`, `test/`, node_modules, `.venv`, runtime db files.
- Unified container/image/volume naming on `finally_agents` / `finally-data-agents` across
  `start_mac.sh`, `stop_mac.sh`, `start_windows.ps1`, `stop_windows.ps1` and `docker-compose.yml`
  (Windows scripts and compose still used the old `finally` names and would have run a second,
  separate instance on a different volume).
- Scripts now: check the Docker daemon, are idempotent (`docker rm -f` on the name), honour `PORT`,
  poll `/api/health` before announcing, print the URL, preserve the data volume on stop.
- Fixed a real macOS bug: `"${ENV_FILE_ARGS[@]}"` aborted under `set -u` on stock bash 3.2 when no
  `.env` exists; guarded with `${a[@]+"${a[@]}"}` and verified the no-`.env` path end to end.
Verified by running, not by inspection:
- `docker build -t finally_agents .` succeeds (frontend static export + backend).
- Container serves UI and API on 8000: `/` 200, `/_next/static/chunks/*.js` 200, `/api/health` ok,
  watchlist/portfolio seeded, `/api/stream/prices` streams SSE.
- Fresh volume -> pristine seed: cash 10000.0, no positions, 10 default tickers.
- Persistence: buy TSLA -> destroy + recreate container on same volume -> position and cash survive.
- `LLM_MOCK=true` chat returns the deterministic mock response.
- `docker compose up -d` / `down` works; `down` preserves the named volume.
- Start/stop scripts re-run cleanly (idempotent); `docker inspect` health reports `healthy`.
Font/build-network question (raised by frontend-engineer): keeping `next/font/google` as-is.
The build stage already needs egress for `npm ci` and `uv sync`, so the font fetch adds no new
build dependency; and Next self-hosts the font at build time, so the RUNTIME container has no
external font dependency. Verified in the image: woff2 files present under
static/_next/static/media/, zero fonts.googleapis.com / fonts.gstatic.com references in
index.html, and the running container serves the woff2 itself (200).

E2E result (integration-tester, against this image): 33/33 green from a clean volume with
LLM_MOCK=true, via `docker compose -f test/docker-compose.test.yml down -v` -> `up -d --build app`
-> `npx playwright test`. Container reports healthy; static UI and /api both serve on 8000; DB
seeds lazily on the /app/db volume.

Locked-in image contract (do not change without telling integration-tester — their first project
asserts pristine seed state and would report a false failure):
- `FINALLY_DB_PATH=/app/db/finally.db` stays, so a clean volume always resets to seed state.
- Runtime image intentionally ships NO curl/wget; healthchecks use python/urllib on both sides.

In flight: nothing
Blocked on: nothing
Ownership clarification (for the team lead):
- TEAM.md grants devops `docker-compose*.yml`, but PLAN.md §12 places the E2E stack under `test/`.
  Agreed with integration-tester that THEY own `test/docker-compose.test.yml` (test infrastructure)
  and devops owns the root `docker-compose.yml` (shipping infrastructure). I read but do not edit theirs.
Interface changes:
- Image env now sets `FINALLY_DB_PATH=/app/db/finally.db` explicitly (same location as before,
  previously derived from a relative path). Nothing else changed for other agents.
Cross-checked integration-tester's compose against the real image: `up -d app` reaches `healthy`;
querying from inside the container gives cash 10000.0, 0 positions, the 10 default tickers,
`/` 200, and the LLM_MOCK chat response. Confirmed the runtime image ships NO curl and NO wget,
so their `python -c urllib.request` healthcheck is correct and stays.

Note for the team: a local uvicorn dev server is bound to 127.0.0.1:8000 on this machine. Docker will
bind 0.0.0.0:8000 alongside it, so anything testing "localhost:8000" may silently hit the dev server
instead of the container. Use a different host port (`PORT=8100 ./scripts/start_mac.sh`) or check
`lsof -nP -iTCP:8000 -sTCP:LISTEN` first. Demonstrated live: with the test stack up,
`curl 127.0.0.1:8000/api/portfolio` returned the dev server's dirty DB (cash 9200.17, an NVDA position)
while `curl localhost:8000/api/portfolio` returned the container's clean state (cash 10000.0, no
positions) — `localhost` resolved to ::1/Docker, `127.0.0.1` to the dev server. Containerised runners
that talk to `http://app:8000` over a compose network are unaffected.
