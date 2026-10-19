# CareerOS-Pro — Phase, Bug & Status Register

Deep audit register. Bugs are tracked per phase with **Status**, **Upgrade**, and **Degradation** columns. Companion to `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/DEPLOYMENT.md`, and `docs/CONFIGURATION.md`.

## Verification baseline (green)

- `ruff check backend/ tests/`: clean
- `pytest`: 315 passed, 1 skipped
- `oxlint` (frontend): clean
- `npm run build`: 433 modules, ~108 kB gzip

---

## Phase 1 — Production hardening & multi-source engine

Status: **shipped**

| Bug | Status | Upgrade | Degradation |
|---|---|---|---|
| Eligibility decided by LLM instead of rules | Fixed | Deterministic normalization, dedup, hard filters; LLM only explains approved matches | None |
| Unbounded multi-source fan-out | Fixed | Concurrent adapters with per-source failure isolation | None |
| No credential-optional local mode | Fixed | `JOB_API_MODE=mock` runs with zero API keys | None |

---

## Phase 2 — Deep security & performance audit

Status: **shipped**

| Bug | Status | Upgrade | Degradation |
|---|---|---|---|
| `create_all()` created tables but not indexes | Fixed (`99bbe70`) | Index creation pass via `checkfirst=True` | Column types still need manual `ALTER` |
| Deploy ran `Base.metadata.create_all()` against an empty registry, creating zero tables | Fixed | `backend/core/db.py:create_all()` imports models first | None |
| `APP_ENV=production` served unauthenticated traffic (empty `API_KEY`) | Fixed | Refuses to start unless `API_KEY` is set | None |
| SSRF via attacker-supplied apply URLs reaching `169.254.169.254` | Fixed | `backend/core/netguard.py` httpx hook re-checks every redirect hop | None |
| Adzuna/Gemini credentials echoed into API responses | Fixed | Secrets scrubbed before logging or return | None |
| `POST /jobs/match` raised `AttributeError` on `req.sources` | Fixed | `sources` added to `MatchRequest`, threaded into graph | None |
| Qdrant client leaked its connection pool on shutdown | Fixed | Lifespan closes reusable HTTP clients + Qdrant + checkpointer | None |
| Blocking DB calls on the event loop | Fixed | Sync DB work moved off the event loop | None |
| One `SELECT ... FROM jobs` per returned row (N+1) | Fixed | Rows materialized before rollback; 3 SELECTs regardless of count | None |
| Pagination tiebreak missing — equal sort keys could repeat/skip pages | Fixed | Added `Job.id.desc()` tiebreaker | None |
| Retention TOCTOU: could delete rows already deemed ineligible | Fixed | Re-checks eligibility, reports real `rowcount` | None |
| Failed job rolled back the entire ingest batch | Fixed | One savepoint per posting | None |

---

## Phase 3 — Code quality & optimization

Status: **shipped**

| Bug | Status | Upgrade | Degradation |
|---|---|---|---|
| Gemini built a new HTTP client per call | Fixed | One reusable client with explicit timeouts, closed on shutdown | None |
| Qdrant collection init ran on every index/search | Fixed | Cached per-process | None |
| Embedding vectorizer not cached | Fixed | `lru_cache` | None |
| Qdrant singleton published before collection init | Fixed | Published only after successful init | None |
| Frontend overrode server's verification verdict | Fixed | Server result is authoritative | None |
| Frontend never sent `salary_max` | Fixed | Correct request field wired through | None |
| Dead code / TODOs / FIXMEs | Fixed | None remain in backend or frontend | None |

---

## Phase 4 — Source-name consistency

Status: **shipped** (`b7958a5`)

| Bug | Status | Upgrade | Degradation |
|---|---|---|---|
| `sources: ["company_scraper"]` excluded the company scraper from fan-out | Fixed | Alias normalized in `get_adapter()` (`company_scraper` ↔ `company`) | None |

---

## Known limitations (deliberate, accepted)

- Local MySQL unavailable in this environment; match tests use the SQLite session fixture.
- Rate limiting is per-process unless `REDIS_URL` is set.
- Checkpointing degrades to in-memory on MySQL 9.x unless `CHECKPOINT_STRICT=true`.
- `create_all()` creates tables/indexes but does not ALTER columns.
- `location` uses SQL `LIKE` (short codes like `in`/`ny` can over-match; authoritative Python pass rejects them).
- Vector store falls back to local in-process implementation.
- Human-review interrupt is wired; resume path not yet exposed over HTTP.

---

## Critical issues fixed (this pass)

- None new — all previously identified critical issues remain resolved and guarded by tests.
