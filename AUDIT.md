# CareerOS-Pro — Deep Audit Report

Date: 2026-10-18

## Summary

Second pass over backend (FastAPI/SQLAlchemy/MySQL), frontend (React/Vite), native
deployment, CI, and docs. The theme was **silent failure**: most defects here did not
crash. They returned `200`, exited `0`, or rendered fine while doing nothing useful.

## Critical

- **`create_all()` created tables but not indexes.** It left an existing table
  entirely alone, indexes included, so adding `index=True` to a model and
  re-running the documented deploy command changed nothing — the missing index
  surfaces only later as a slow query. The live local database was missing **six
  of the nine** declared indexes because of this. `create_all()` now also creates
  missing indexes (`checkfirst=True`, so re-running is a no-op). Column types still
  need a manual `ALTER`; this pass covers tables and indexes.
- **Deploying created no schema, and reported success.** Every deploy doc said to run
  `Base.metadata.create_all(engine)`. `Base` is an empty registry until a model module
  imports, so that command exited `0` having created zero tables. Every other test
  imported `backend.models` first as a side effect and masked it. Fixed with
  `backend/core/db.py:create_all()`, which imports the models before creating. The
  broken and fixed forms are both demonstrated in
  `tests/test_hardening.py::test_documented_schema_command_actually_creates_tables`.
- **`APP_ENV=production` served unauthenticated traffic.** `require_api_key` is a no-op
  on an empty key, and `.env.example` ships one empty. A production deployment was
  publicly writable. Now refuses to start.
- **SSRF via attacker-supplied URLs.** `POST /jobs/{id}/verify` and the company
  scraper fetched caller-influenced URLs with no restrictions — reaching
  `169.254.169.254` (cloud metadata, i.e. instance credentials) and anything on the
  internal network. Added `backend/core/netguard.py`, wired in as an httpx request
  hook so it re-checks **every redirect hop**, not just the first URL.
- **Adzuna credentials could leak into API responses.** Source errors echoed raw
  provider payloads through `/agents/health`. Now scrubbed before logging or return.

## Correctness

- **A failed job rolled back the entire ingest batch.** Now one savepoint per posting.
- **LLM error handling swallowed failures silently.** Gemini failures now log.
- **`get_adapter(sources=...)` ignored the request**, always using all configured
  sources — a request narrowed to one source still queried six.
- **Explanation loading was N+1** (one query per explained job) and unbounded by
  `top_k`. Now one batch query, capped at 20 explanations.
- **Gemini built a new HTTP client per call.** Now one reusable client with explicit
  connect/read timeouts, closed on shutdown.
- **Qdrant collection init ran on every index and search.** Now cached per process.
- **Retention could delete rows it had already deemed ineligible** (TOCTOU between
  select and delete). Now re-checks, and reports the real `rowcount`.
- **Pagination had no tiebreaker**, so rows with equal sort keys could repeat or skip
  across pages. Added `Job.id.desc()`.
- **Every response re-SELECTed every row it returned.** All three handlers called
  `db.rollback()` — which expires every ORM instance in the session — and then
  returned live ORM objects. FastAPI's `response_model` validation touched those
  expired attributes *after* the handler returned, one SELECT per row, on the
  event loop. Measured on `/jobs/search`: 22/62/200 rows cost 27/65/203
  `SELECT ... FROM jobs`. The explain node in the match graph had it too, hidden
  inside an `asyncio.gather` fan-out. Responses are now materialised before the
  rollback: 3 SELECTs regardless of row count.
- **Blocking PyMySQL on the event loop.** `async def` handlers and graph nodes ran
  their DB work inline; `/jobs/search` is ~1000 round-trips per request, during
  which no other request could be served. The blocking halves now run via
  `asyncio.to_thread`. The data layer is still sync PyMySQL — a full async-SQLAlchemy
  migration remains unnecessary at this scale.

## Frontend

Three genuine React bugs, found by adding an `oxlint --deny correctness` gate:

- `SegmentedControl` read `refs.current[value]` **during render** — null on first
  render (pill at zero width) and stale after a value change. Now measured in a
  layout effect and held as state.
- `useReducedMotion` set state in an effect, so every consumer re-rendered after
  hydration and animations briefly ran at full motion. Now `useSyncExternalStore`,
  which subscribes directly to `matchMedia`.
- `JobCard` mirrored a prop into state via an effect. Now adjusted during render, per
  React's documented pattern.

## Infrastructure

Docker was removed after the audit below was written — the project runs natively now
(see `deploy/`). Findings that no longer describe a shipped file:

- **`.dockerignore` did not exist.** `backend/venv` (585 MB, 8454 `.py` files, macOS
  wheels) sat inside a `COPY backend/` line and was baked into every image, with
  wheels wrong for the image platform.
- **`qdrant/qdrant:latest`** — an unattended `pull` could change the on-disk format and
  API across a minor version. Pinning was the fix at the time; the dependency is gone.
- **A healthcheck interpolated `$line` in a way Compose consumed itself**, so the check
  never tested what it claimed to. Verified against a live socket instead (200 → pass,
  503 → fail). The current systemd unit asserts on `/health/ready` for the same reason.
- **No log rotation, and no graceful stop** — the 10s default cuts off in-flight
  requests. The systemd unit sets `TimeoutStopSec=45` and `KillSignal=SIGINT`; journald
  rotates.

Still true after Docker left:

- **`requirements.txt` used `>=` ranges**, so the same commit resolved different
  packages on different days. Now exactly pinned to the versions the suite passed
  against, with a CI gate.

## Test suite

- `test_boundaries.py` parametrized over *every* backend module and skipped 54 of
  them inside the test body — 23% of the reported count was noise, and it had a false
  negative: a nested file like `services/matching/deep/x.py` was skipped even though it
  is exactly the case the rule exists to catch. Now pre-filters per rule; **0 skips**.
- Added `tests/test_hardening.py` — 21 tests pinning the production invariants above.

## Known Limitations (deliberate, not deferred-by-accident)

- **`get_db()` yields a sync `Session`/PyMySQL.** Handlers and graph nodes offload
  that work to a worker thread, so it no longer stalls the loop, but a slow query
  still occupies a thread and the engine stays sync. Migrating the data layer to
  async SQLAlchemy (`aiomysql`) would remove the thread hop; it is a large change
  with its own correctness risk, and unnecessary at single-host scale.
- **No migration tool.** `create_all()` creates missing tables but will not *alter*
  existing ones. Widening a column needs a reviewed manual `ALTER` per environment.
- **Frontend has no test framework** — no Vitest, no jsdom, no component tests. The
  gate is `oxlint --deny correctness` plus the production build. A test suite was
  skipped rather than added thin.
- **Redis optional.** Per-process rate-limit counters under-limit by the worker count.
  Run one worker, or install Redis natively and set `REDIS_URL`.
- **The declared SQL/Python filter over-match for `location` needles** (`ny`, `in`) is
  kept and tested, not fixed — word boundaries need dialect-specific SQL, and the Python
  pass narrows it back. One intentional skip, guarded by a test asserting the
  over-match still exists.

## Verification

| Gate | Result |
|---|---|
| `ruff check backend/ tests/` | clean |
| `pytest` | 263 passed, 1 skipped (intentional) |
| `oxlint --deny correctness src/` | clean |
| `npm run build` | 433 modules, 108 kB gzip |
| Pinning gate | pass |
| Every CI command re-run locally | pass |

The one remaining warning is upstream: Starlette's test client warns that `httpx` is
deprecated in favour of `httpx2`.