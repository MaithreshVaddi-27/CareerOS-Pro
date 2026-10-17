# Deep Audit

Status of the codebase: verified weaknesses, how the system degrades, and the
recommended upgrade path.

- **Scope:** `backend/` (25 Python files), `frontend/src` (24 files), CI, config,
  dependencies.
- **Method:** static analysis (`ruff`, `pyflakes`), import-graph tracing, runtime
  probes of each degradation path, call-path review of the search and match paths.
- **Everything marked "verified" below was reproduced on this tree**, not inferred.

Severity: **P0** breaks correctness or security · **P1** breaks at scale or
silently degrades quality · **P2** correctness-adjacent or maintenance debt.

---

## Summary

| | Count |
|---|---|
| Fixed in this pass | 12 |
| P0 open | 1 |
| P1 open | 5 |
| P2 open | 7 |

**Verdict.** The search and match pipeline is sound and its determinism guarantees
hold. The two things that matter before production are **A1** (no migrations — schema
changes require manual DDL) and **A2** (rate limiting is per-process, so workers
multiply the limit). Neither is exploitable, but both bite at exactly the moment you
scale out.

---

## Resolved in this pass

Twelve findings fixed and regression-tested. Listed for traceability, not action.

| # | Finding | Severity | Resolution |
|---|---|---|---|
| R1 | `_extract_company` fell back to the **entire card text**, corrupting `company` and therefore `content_hash` | P0 | Falls back to the requested company name. Card text never enters the field. |
| R2 | Location `City, Region` regex ran against a **lowercased** string, so it could never match — every scraped location was empty | P0 | Runs against original casing; unit-tested. |
| R3 | Scraper emitted a **pre-mapped** employment type; `parse_employment_type` then failed the lookup and returned `unknown` | P0 | Adapter emits raw tokens, per the `_raw` contract. `contract-to-hire` added to the map. |
| R4 | `location_normalized` was `.title()`-cased — `"New York, NY"` → `"New York, Ny"` | P1 | Whitespace collapsed only; source casing preserved. Filtering lowercases both sides. |
| R5 | `--location` / `location` argument accepted but **never applied** — a silent no-op | P1 | Implemented as a case-insensitive filter. |
| R6 | `apply_url` fell back to a **sibling card's anchor**, colliding on `url_hash` | P0 | In-card links only; stable `slug-index` fallback otherwise. |
| R7 | New `httpx.AsyncClient` constructed **per retry attempt** — no keep-alive | P2 | One client per scraper, reused. |
| R8 | HTML cache had **no expiry** — a closed role stayed listed indefinitely | P1 | TTL cache, `0` disables. |
| R9 | `4xx` was retried, so a `403` cost three full attempts | P2 | 4xx fails fast. |
| R10 | Dead megacorp fallback scraped Google/Microsoft/Apple/Amazon/Meta on **every empty search** | P1 | Removed. Company adapter fires only on a named company. |
| R11 | `ruff check` in CI was **unpinned and red** — ruff 0.17 expanded defaults from ~5 rules to 400+ | P0 | Rule set pinned in `pyproject.toml`, ruff pinned in CI. |
| R12 | `evidence_and_explain` was **serial** — `/jobs/match` latency `O(top_k × provider)` | P1 | Bounded fan-out (`_EXPLAIN_CONCURRENCY = 5`). |

### Also cleaned

- Dead frontend assets (`hero.png`, `react.svg`, `vite.svg`) and unused `Card.jsx`
- `test_hash_url` / `test_hash_content` duplicated across two files; kept the
  stronger versions and moved them to their correct owner
- Unused dependencies: `alembic`, `slowapi`, `gradio`, `respx`
- Vestigial `gradio_username` / `gradio_password` settings (there is no Gradio UI)
- Dead `_is_career_page` / `_normalize_career_url` / `_normalize_location` (the last
  duplicated `normalize.parse_location`)
- Stale comments naming pre-restructure module paths

---

## Open findings

### <a id="a1-no-database-migrations"></a>A1 — No database migrations · P0

**Verified.** There is no `alembic/`, no `migrations/`, and no migration tooling.
`core/db.py:sync_schema()` reconciles schema at startup, and it only ever **widens
`VARCHAR` columns**:

```python
if current_len is not None and current_len < target_len:
    txn.execute(text(f"ALTER TABLE jobs MODIFY COLUMN `{col.name}` VARCHAR({target_len})"))
```

**What this cannot do:**

- Add a `NOT NULL` column without a default
- Drop or rename a column
- Change a column type
- Add an index or a constraint
- Reorder columns

**Impact.** Any schema change beyond widening a `VARCHAR` requires a human running
`ALTER TABLE` by hand against every environment. On a table with real data, adding a
`NOT NULL` column without a default fails outright. Two environments silently drift
apart, and there is no record of what the schema *should* be.

There is already scar tissue from this: the docstring in `sync_schema()` documents an
actual production incident — `Data too long for column source_id`. That is a symptom of
schema drift with no migration path, and it will recur.

**Fix.** Adopt Alembic. `alembic` is already in the historical dependency list and
removed in this pass as unused — re-add it with a real `alembic.ini` and an initial
revision that matches the current models. Then delete `sync_schema()` once production
has migrated. Estimated: **M**, mostly mechanical.

**Interim mitigation.** Until migrations exist, treat any `models/job.py` column change
as requiring a reviewed manual `ALTER TABLE`, and note it in the PR description.

---

### <a id="a2-rate-limiting-is-per-process"></a>A2 — Rate limiting is per-process · P1

**Verified.** `core/middleware.py` keeps counters in module-level dicts:

```python
_request_counts: dict[str, deque] = defaultdict(deque)
_burst_counts: dict[str, deque] = defaultdict(deque)
```

**Impact.** With `--workers 4`, the effective limit is **4× `RATE_LIMIT_PER_MINUTE`**.
Each worker counts independently; none sees the others' traffic.

Related: the client IP comes from the request. Behind a reverse proxy, if
`X-Forwarded-For` is not forwarded and trusted, **every** request appears to originate
from the proxy — so all clients collapse into one shared bucket and the limit applies
globally rather than per client.

**Fix.** Move counters to shared storage — Redis `INCR` with `EXPIRE` is the standard
answer. **M** once Redis is already a dependency; **S** if it is not.

**Interim mitigation.** Run a single worker. If you cannot, set
`RATE_LIMIT_PER_MINUTE` to one quarter of the intended limit and document that the
number is per-worker.

---

### <a id="a3-silent-checkpoint-downgrade"></a>A3 — Checkpoint downgrade is silent in production · P1

**Verified.** `graph.py:get_checkpointer()` falls back to `MemorySaver` on any
exception:

```python
except Exception as exc:
    logger.warning("MySQL checkpointer setup failed (%s) — falling back to MemorySaver…", exc)
    saver = MemorySaver()
```

**Impact.** On MySQL 9.x, `AIOMySQLSaver.setup()` fails (MD5 disallowed in generated
columns). The app stays up and **looks healthy**, but checkpoints are lost on every
restart and not shared across workers. For a workflow with a human-review interrupt,
that means in-flight reviews silently vanish.

The health endpoint does **not** surface this — `/health` reports database status, not
checkpoint status.

**Fix.** Report checkpoint type on `/health`, and add a `CHECKPOINT_STRICT=true` setting
that refuses to start rather than degrading. **S**.

---

### <a id="a4-full-table-scan-per-request"></a>A4 — Every match loads the whole jobs table · P1

**Verified.** `graph.py:105`:

```python
all_jobs = db.execute(select(Job)).scalars().all()
filtered = apply_hard_filters(all_jobs, SearchRequest(...))
```

**Impact.** Every `/jobs/match` request loads **every row** in `jobs` as a full ORM
object, then filters in Python. With 10k jobs this is ~10k ORM instantiations per
request; at 100k it is unusable. `apply_hard_filters` is a list comprehension over
Python objects (`filters.py:150`), so none of the filtering is pushed to SQL.

This is the single biggest scaling constraint in the system.

**Fix.** Push the hard filters into the `WHERE` clause. `filters.py` is already
written as per-row predicates, so this is mostly a matter of translating each to a SQL
expression and indexing the columns that matter (`employment_type`,
`experience_level`, `remote`, `salary_min`, `posted_at`, `location_normalized`).
**M**.

**Interim mitigation.** Prune `is_active = false` rows on a schedule, and keep
retention bounded.

---

### <a id="a5-no-pagination"></a>A5 — No pagination · P2

**Verified.** Neither `/jobs/search` nor `/jobs/match` accepts a `limit`/`offset`.
`/jobs/match` is bounded by `top_k` (default 10); `/jobs/search` is not bounded at
all.

**Impact.** A broad query returns the entire matching set. Past a few thousand rows the
response is both slow and impractical for a client to render.

**Fix.** Add `limit`/`offset` (or cursor pagination) to `SearchRequest`, and an explicit
cap on `/jobs/search`. **S**.

---

### <a id="a6-career-page-scraping-has-no-robots-tolerance"></a>A6 — Career-page scraping ignores robots.txt · P2

**Verified.** No reference to `robots.txt` or `urllib.robotparser` anywhere in
`backend/`. The scraper fetches any URL it is given and probes paths
(`/careers`, `/jobs`, `/talent`, …) that were not linked from anywhere.

**Impact.** The README calls the scraper "polite", and the per-domain semaphore, jitter,
TTL cache and configurable `User-Agent` genuinely reduce load — but none of that is a
substitute for respecting `robots.txt` or the target's terms of use. Probing unlinked
paths on sites that do not offer a public careers feed is the part most likely to draw a
block or a complaint.

**Fix.** `urllib.robotparser.RobotFileParser` is stdlib and roughly 15 lines: fetch
`/robots.txt` per host, cache the verdict, and refuse disallowed paths. **S**.

**Interim mitigation.** Only scrape companies that publish an open careers board
(Greenhouse, Lever, Ashby, Workday public boards) — these are explicitly intended for
machine consumption, and `COMPANY_CAREER_PAGE_PATTERNS` can point at them directly.

---

### A7 — N+1 query in the explain node · P2

**Verified.** `graph.py:158` calls `db.get(Job, entry["job_id"])` once per ranked
entry, after the batch fetch in `_retrieve_and_rerank` already loaded those rows.

**Impact.** Bounded by `top_k` (default 10), so it is ~10 extra identity-map lookups per
request. Minor, and the identity map absorbs most of it. Worth fixing for tidiness, not
for performance.

**Fix.** Carry the loaded `Job` objects through `ranked` instead of re-fetching. **S**.

---

### A8 — Test coverage gaps · P2

**Verified.** No test file imports `backend.main`, `backend.cli`, `backend.core.db`,
`backend.core.middleware` or `backend.core.monitoring`.

Untested and worth covering:

| Module | What is untested |
|---|---|
| `main.py` | All routes, auth gate, error shapes. The `X-API-Key` check has no test. |
| `core/middleware.py` | Rate-limit enforcement, security headers, request-id |
| `core/monitoring.py` | `AgentStats.status` thresholds |
| `core/db.py` | `sync_schema()` widening logic |
| `graph.py` | `_discover_normalize_dedup_filter`, `_retrieve_and_rerank`, `build_graph` |
| `cli.py` | Salary filtering, output shape |

`core/middleware.py` is the priority: the rate limiter is security-relevant and
currently has no test at all.

---

### A9 — `skills_required` is always empty · P2

**Verified.** `normalize.py:normalize_job()` sets
`skills_required=[]` with the comment "filled by skill-extraction step (LLM/ambiguity
layer), not here". No such step exists anywhere in the codebase.

**Impact.** `JobOut.skills_required` and the Qdrant index are always empty. Semantic
search quality depends on job text embeddings, so ranking still works — but anything
consuming `skills_required` gets nothing.

**Fix.** Either extract skills deterministically (a curated dictionary, matching the
`unknown`-stays-`unknown` rule) or drive it from the existing grounded LLM layer.
**M** for the dictionary approach.

---

### A10 — No human-review resume path over HTTP · P2

**Verified.** The graph compiles with `interrupt_before=["human_review"]`, and
`CareerOSState` carries `approved_ids` / `rejected_ids`. No route accepts a resume
command, and no code writes to those keys.

**Impact.** The workflow always halts at the interrupt. `/jobs/match` returns the
ranked list and nothing can act on it — by design (nothing auto-applies), but the
resume half is unimplemented.

**Fix.** A `POST /jobs/match/{thread_id}/resume` route taking approve/reject IDs and
resuming with `Command(resume=…, update=…)`. **S**.

---

### A11 — Health endpoint reports no component-specific detail · P3

**Verified.** `/health` returns a flat status object with no version, commit, or
uptime. `checks.rate_limit` can only ever be `ok` or `configurable` — it never reports
`degraded`, so the branch that would downgrade on it is dead.

**Fix.** Add build metadata, and surface checkpoint type (see A3). **S**.

---

### A12 — `settings()` is module-scope in nine modules · P3

**Verified.** Nine modules call `get_settings()` at import time. The value is
`@lru_cache`d so it is cheap, and this is the standard `pydantic-settings` pattern —
but it means the environment is snapshotted at import, which is why tests must call
`get_settings.cache_clear()`.

**Impact.** Negligible in production. A surprise only when a module is imported before
the environment is fully populated.

**Fix.** None needed. Documented so it is not "fixed" later.

---

## Degradation behaviour

What the system does when a dependency fails. **All rows below were verified by
running the failure path**, not inferred.

| Failure | Behaviour | User-visible effect |
|---|---|---|
| **No MySQL** | App does not start | Hard failure. Nothing can degrade without the database. |
| **MySQL unreachable at request time** | `500` on DB-backed routes | Hard failure. `/health` reports `degraded`. |
| **No LLM provider configured** | Ranking still runs; every explanation is `{matched_skills: [], missing_skills: [], explanation: "unknown — no LLM provider available or all providers failed", confidence: 0.0}` | **No crash.** Scores and ordering unaffected. |
| **All LLM providers fail mid-request** | Same as above | Same |
| **One LLM provider fails** | Router falls through to the next | None |
| **LLM returns `confidence < 0.6`** | Router treats it as a miss, tries the next provider | None, unless all providers are low-confidence |
| **LLM returns malformed JSON** | Pydantic validation fails → caught → next provider | None |
| **One job source fails** | Logged, counted in `/agents/health`, contributes `[]` | None. Other sources still return. |
| **All job sources fail** | `200` with `[]` | Empty result, **not** an error |
| **`JOB_API_MODE=mock`** | Deterministic fixtures, no network I/O | Fixed result set |
| **Credential missing for a source** | Adapter is never constructed | Source silently absent |
| **Career page unreachable / blocked** | Retries then returns `[]` | No company results. Other sources unaffected. |
| **Career page 404** | Fails immediately (no retry) | No company results |
| **`QDRANT_URL` unset** | Embedded/local Qdrant instance | Semantic search still works, single-process |
| **`FIRECRAWL_API_KEY` unset** | `/jobs/{id}/verify` returns `422` | Postings stay `verified: false`. **Not** an error state to trust. |
| **Posting is dead but unverified** | Nothing detects it | The user discovers it by following `apply_url` |
| **MySQL 9.x** | Checkpoint setup fails → in-memory fallback, warning logged | App works; **checkpoints lost on restart** (A3) |
| **Rate limit exceeded** | `429` + `Retry-After` | Client must back off |
| **Rate limit + multiple workers** | Each worker counts separately | Effective limit is N× (A2) |

### The design rule behind this table

> A degrading dependency must produce **fewer results or thinner explanations** —
> never a wrong answer and never a crash.

The one soft spot is verification: an unverified posting is reported as `verified:
false`, which is honest but easy to misread as "still live". Anything in the UI that
renders postings should treat `verified: false` as *unknown*, not as *good*.

---

## Upgrade path

Ordered by dependency and risk. Each step is independently shippable.

### Phase 1 — Safety net (before scaling out)

| # | Item | Fixes | Effort |
|---|---|---|---|
| 1 | Alembic migrations; initial revision matching current models | A1 | M |
| 2 | Shared-store rate limiting | A2 | M |
| 3 | Surface checkpoint type in `/health`; `CHECKPOINT_STRICT` | A3 | S |

### Phase 2 — Scale

| # | Item | Fixes | Effort |
|---|---|---|---|
| 4 | Push hard filters into SQL; index the filtered columns | A4 | M |
| 5 | `limit`/`offset` on `/jobs/search` and `/jobs/match` | A5 | S |
| 6 | Bounded job retention; prune `is_active = false` rows | A4 | S |

### Phase 3 — Quality

| # | Item | Fixes | Effort |
|---|---|---|---|
| 7 | Deterministic `skills_required` extraction | A9 | M |
| 8 | Tests for `core/middleware.py` (rate limit, auth, headers) | A8 | S |
| 9 | Tests for the remaining untested routes and graph nodes | A8 | M |
| 10 | robots.txt compliance in the scraper | A6 | S |
| 11 | Resume endpoint for the human-review interrupt | A10 | S |

### Phase 4 — Hardening

| # | Item | Fixes | Effort |
|---|---|---|---|
| 12 | Structured/JSON-LD job extraction (`JobPosting` schema) | scraper accuracy | M |
| 13 | Frontend test suite (`npm run build` is currently the only signal) | A8 | M |
| 14 | N+1 fix in the explain node | A7 | S |
| 15 | Build metadata and richer health output | A11 | S |

### Effort key

**S** — under an hour. **M** — half a day. **L** — multi-day.

---

## Standing risks

Things that are not bugs but will bite:

1. **Scraper CSS selectors are a maintenance treadmill.** Company career pages redesign
   constantly. `JOB_CARD_SELECTORS` is an ordered list of guesses. Phase 4 item 12
   (JSON-LD) is the real fix; per-ATS configuration via
   `COMPANY_CAREER_PAGE_PATTERNS` is the stopgap.
2. **Ruff's default rule set is a moving target.** Pinning it is the whole reason
   `pyproject.toml` exists. Do not "simplify" by deleting the config.
3. **Source APIs are free tiers with undocumented quotas.** Expect a source to start
   rate-limiting without notice. The degradation table covers it, but alert on
   `/agents/health` failure rates rather than discovering it in a search result.
4. **Provider fallback is silent about *why*.** A user sees `confidence: 0.0` with no
   indication whether no provider was configured or all four failed. Surfacing the
   reason would cost nothing and would save real debugging time.

---

## Verifying this audit yourself

```bash
source backend/venv/bin/activate
pip install "ruff==0.17.1"

ruff check backend/ tests/     # lint — must be clean
python -m pytest -v           # tests — must pass

# Dependency claims
for d in alembic slowapi gradio respx; do
  printf '%s: ' "$d"; rg -c "import $d|from $d" backend/ --glob '!venv' || echo 0
done

# The full-table scan (A4)
rg -n "select\(Job\)" backend/graph.py

# Per-process rate limiting (A2)
rg -n "_request_counts" backend/core/middleware.py

# Missing migrations (A1)
find . -name alembic.ini -o -name "migrations" -type d | grep -v venv

# Untested modules (A8)
for m in main cli core.db core.middleware core.monitoring; do
  printf '%s: ' "$m"; rg -l "backend.$m" tests/ 2>/dev/null | wc -l
done
```