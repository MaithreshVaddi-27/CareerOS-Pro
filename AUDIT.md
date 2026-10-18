# CareerOS-Pro — Deep Audit Report

Date: 2026-10-18

## Summary

Second pass over backend (FastAPI/SQLAlchemy/MySQL), frontend (React/Vite), Docker
Compose, CI, and docs. The theme was **silent failure**: most defects here did not
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
- **Sync PyMySQL calls inside `async def`** block the single event loop. Real
  limitation, deliberately not rewritten — see Known Limitations.

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

- **`.dockerignore` did not exist.** `backend/venv` (585 MB, 8454 `.py` files, macOS
  wheels) sat inside the `COPY backend/` line and was baked into every image — with
  wheels wrong for the image platform. Added, plus dropped the unused ~500 MB
  `build-essential`.
- **`qdrant/qdrant:latest`** — an unattended `pull` could change the on-disk format and
  API across a minor version. All three images now pinned to exact patches.
- **Backend waited on `service_started` for Qdrant**, which binds its port before
  collections finish loading. Now gated on a real `/readyz` healthcheck.
- **`$line` in the new healthcheck was interpolated by Compose itself.** Escaped to
  `$$line`; the check is verified against a live socket (200 → pass, 503 → fail).
- No log rotation on any service (a chatty container fills a small VPS disk), and no
  `stop_grace_period` (10s default cuts off in-flight requests).
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

- **Sync SQL driver on the async event loop.** `get_db()` yields a sync
  `Session`/`PyMySQL`, so a slow query stalls every concurrent request. Fixing this
  means async SQLAlchemy (`aiomysql`) across the whole data layer — a large change with
  its own correctness risk. Single-host and small-scale it is a throughput ceiling, not
  a correctness bug. Documented rather than half-migrated.
- **No migration tool.** `create_all()` creates missing tables but will not *alter*
  existing ones. Widening a column needs a reviewed manual `ALTER` per environment.
- **Frontend has no test framework** — no Vitest, no jsdom, no component tests. The
  gate is `oxlint --deny correctness` plus the production build. A test suite was
  skipped rather than added thin.
- **Redis optional.** Per-process rate-limit counters under-limit by the worker count.
  Documented in the compose file; the `ratelimit` profile fixes it.
- **The declared SQL/Python filter over-match for `location` needles** (`ny`, `in`) is
  kept and tested, not fixed — word boundaries need dialect-specific SQL, and the Python
  pass narrows it back. One intentional skip, guarded by a test asserting the
  over-match still exists.

## Verification

| Gate | Result |
|---|---|
| `ruff check backend/ tests/` | clean |
| `pytest` | 261 passed, 1 skipped (intentional), 5.3s |
| `oxlint --deny correctness src/` | clean |
| `npm run build` | 433 modules, 108 kB gzip |
| `docker compose config` | valid |
| Pinning gate | pass |
| Every CI command re-run locally | pass |

The one remaining warning is upstream: Starlette's test client warns that `httpx` is
deprecated in favour of `httpx2`.