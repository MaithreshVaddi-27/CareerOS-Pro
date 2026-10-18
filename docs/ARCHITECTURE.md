# Architecture

How CareerOS is put together, why, and where to change things.

---

## 1. Design constraints

Four constraints drove almost every structural decision. Read these first — most
"why is it like that" questions are answered here.

### 1.1 Determinism before intelligence

Filtering, normalization, dedup and ranking are pure functions with no model in the
loop. An LLM may explain a ranking; it may never produce one.

Concretely: `_discover_normalize_dedup_filter` and `_retrieve_and_rerank` in
`backend/graph.py` never touch a model router. If a job is filtered out, no amount of
LLM confidence brings it back.

### 1.2 Unknown stays unknown

A field that cannot be read from the source is recorded as `unknown`, `""` or `None`.
It is never inferred, defaulted to a plausible value, or filled from the job title.

This is why `NormalizedJob` carries explicit `unknown` sentinels for
`location_normalized`, `employment_type`, `experience_level`, `remote` and
`salary_currency`. Those are load-bearing values downstream — `filters.py` reads them,
so a fabricated value would silently corrupt user results.

### 1.3 Optional everything

The app must boot and answer with **zero** third-party credentials. Every adapter is
constructed only when its credential is present; every provider returns a graceful
explanation rather than raising. `JOB_API_MODE=mock` performs no network I/O at all.

### 1.4 Services hold the logic

`main.py` and `graph.py` contain no job-data business logic. `graph.py` is pure
orchestration — every node delegates to a service. This is what makes the services
testable without a database or a graph run, and it is why most of the test suite is
plain unit tests.

---

## 2. Module map

```
backend/
├── core/                    cross-cutting; no job knowledge
│   ├── config.py            Settings (pydantic-settings, lru_cache), single source of truth
│   ├── db.py                engine, SessionLocal, Base, create_all()
│   ├── middleware.py        request-id, security headers, rate limit, API-key gate
│   └── monitoring.py        AgentMonitor: per-agent latency/failure stats
├── models/job.py            SQLAlchemy ORM — one table: jobs
├── schemas/job.py           Pydantic contracts (Raw/Normalized/Out/Request)
├── services/                all business logic
│   ├── sourcing/            talks to the outside world about jobs
│   │   ├── job_api_adapter.py  source adapters + fan-out
│   │   ├── company_scraper.py  career-page scraping
│   │   ├── normalize.py        deterministic parsing
│   │   ├── dedup.py            upsert by hash
│   │   └── verification.py     liveness check
│   └── matching/            turns rows we hold into a ranked, explained answer
│       ├── filters.py          hard filters (SQL prefilter + authoritative Python pass)
│       ├── reranker.py         hybrid scoring
│       ├── model_router.py     LLM providers + grounded prompting
│       ├── embeddings.py       embedding providers
│       └── vector_store.py     Qdrant + fallback
├── graph.py                 LangGraph state machine
├── main.py                  FastAPI app, routes, lifespan
├── cli.py                   scrape one company → JSON on stdout
├── retention.py             bounded pruning of inactive jobs
└── run.py                   entrypoint (loads .env, fixes sys.path, starts uvicorn)
```

### The `services/` split

`sourcing` and `matching` are separated by a single question: **does this module talk
to an external system to learn about a job, or does it reason about rows we already
hold?**

- **sourcing** — everything that goes out to the network about jobs, plus parsing,
  dedup, and the liveness check (which also goes out to the network, to ask whether a
  posting we hold is still there).
- **matching** — filtering, semantic recall, scoring, and the grounded explanation.

Nothing in `matching` may fetch a job. That boundary is load-bearing: the graph runs
`discover → normalize → dedup → filter` on every request, and if matching could reach
the network it would re-acquire the same postings per request instead of reading what
dedup already stored.

### Dependency direction

```
main.py ─┐
graph.py ─┼─→ services/* ─┐
cli.py  ───┘               ├─→ core/config.py  (leaf)
retention.py               └─→ schemas/job.py (leaf)
core/*  ─────────────────────→ core/config.py
models/job.py ───────────────→ core/db.py (Base)

services/sourcing/* → services/sourcing/* only  (adapter → scraper)
services/matching/* → services/matching/* only  (vector_store → embeddings)
```

The two `services/` subpackages do not import each other at all. They meet only in
`graph.py`, which orchestrates them in sequence.

Rules that hold everywhere:

- `core/*` never imports from `services/*`.
- `services/*` never imports `main`, `graph`, `cli`, or `retention`.
- `services/matching/*` never imports `services/sourcing/*`, and vice versa.
- `schemas/` and `models/` are leaves — they import only from `core/`.
- Nothing imports from `tests/`.

`backend/core/db.py` holds `Base` and the engine; `models/` import `Base` from it.
That gives a cycle (`db → models → db`), broken by importing `backend.models`
*inside* `create_all()` rather than at module scope.

This is why the function exists instead of a bare `Base.metadata.create_all(engine)`
call at the import site: `Base` is an empty registry until a model module is
imported, so anything that calls `create_all` without importing the models first
creates **zero tables and exits 0**. `tests/test_hardening.py` runs the
documented command in a clean subprocess to keep that failure from returning.

It also loops over `Base.metadata.sorted_tables` and creates each declared index
with `checkfirst=True`. `create_all()` alone leaves an existing table entirely
alone — indexes included — so adding `index=True` to a model and re-running the
deploy command changed nothing on an existing database, and the missing index
only surfaced later as a slow query. The live development database was missing
six of the nine declared indexes because of exactly that. Column types still need
a manual `ALTER`; this pass covers tables and indexes.

---

## 3. Data flow

### 3.1 Normalization pipeline

```
source response
    │
    ▼
adapter.search()          → list[RawJobPosting]
    │                       raw source text preserved verbatim
    ▼
normalize_job(raw)        → NormalizedJob
    │                       normalize_url → url_hash     (sha256, unique index)
    │                       hash_content   → content_hash
    │                       parse_location → (normalized, remote)
    │                       parse_employment_type / parse_experience_level
    │                       parse_salary / parse_posted_at
    ▼
upsert_job(db, job)       → (row, created)
    │                       created=True only → index in Qdrant
    ▼
Job rows in MySQL
```

**Field naming is a contract.** `RawJobPosting` fields ending in `_raw`
(`location_raw`, `employment_type_raw`, `posted_at_raw`, `salary_raw`) hold *source
text*, not normalized values. Adapters must emit raw text. Mapping a value early
causes double normalization: `parse_employment_type("full_time")` looks up a raw key,
misses, and returns `unknown`. The company scraper is the newest adapter and obeys
this rule.

### 3.2 Matching workflow

`backend/graph.py` compiles a linear LangGraph state machine:

```
discover_normalize_dedup_filter
        → retrieve_and_rerank
        → evidence_and_explain
        → human_review          ← interrupt_before
        → END
```

`CareerOSState` is a `TypedDict`:

| Key | Type | Produced by |
|---|---|---|
| `request` | `dict` | caller (`run_match_workflow`) |
| `candidate_ids` | `list[str]` | `discover_…` |
| `ranked` | `list[dict]` | `retrieve_and_rerank`, enriched by `evidence_and_explain` |
| `approved_ids` | `list[str]` | resume path (not yet exposed) |
| `rejected_ids` | `list[str]` | resume path (not yet exposed) |

There are **no conditional edges**. Adding one is a deliberate change and should come
with a test.

#### Why `evidence_and_explain` fans out

Each ranked job needs one LLM round-trip. Run serially, `/jobs/match` latency is
`O(top_k × provider_latency)` — at `top_k=10` and ~2s per call that is ~20s of pure
waiting. The node therefore uses `asyncio.gather` behind a semaphore
(`_EXPLAIN_CONCURRENCY = 5`), capping provider pressure while hiding latency.

The shared `Session` is safe here because `db.get()` is synchronous and completes
before the first `await`, so it is never held across a suspension point.

### 3.3 Checkpointing

`get_checkpointer()` returns:

- `MemorySaver` when `APP_ENV=development`
- `AIOMySQLSaver` (MySQL) otherwise, with a `MemorySaver` fallback **and a warning**
  if setup fails

The fallback is deliberate: MySQL 9.x rejects MD5 in generated columns, which breaks
`AIOMySQLSaver.setup()`. Degrading is better than 500-ing every request — but it does
mean a misconfigured production deploy silently loses checkpoint durability — which is
why `CHECKPOINT_STRICT=true` turns the downgrade into a refusal to start.

---

## 4. Scoring

`reranker.py` combines two independent signals:

```
hybrid = w_keyword · keyword_score + w_semantic · semantic_score
```

- **keyword** — lexical overlap between the query and title/description. Cheap,
  explainable, but brittle to synonyms.
- **semantic** — Qdrant vector similarity between the query embedding and the job
  embedding. Catches paraphrase, but can be confidently wrong on short postings.

The weights are deliberately modest and the two signals disagree often. That
disagreement is signal: a job with strong lexical and weak semantic match is usually a
title match, not a role match.

**LLM confidence is not part of `hybrid_score`.** It is reported alongside, never
folded in — otherwise the ranking stops being reproducible.

---

## 5. The LLM prompt

Built once per job in `_build_match_prompt()`, then reused for every provider attempt
so that routing cannot change the question being asked.

**CRAFT structure.** Role / Context / Action / Format / Tone. The Role and the
`_SYSTEM_CONTRACT` preamble carry the grounding rules; Action gives an ordered
grounding pass; Format pins the JSON shape with a worked example (small local models
drift without one); Tone suppresses hedging prose.

**TOON serialization.** `_toon_block()` emits:

```
job.title:: Staff Backend Engineer
job.description:: Build services in Python and Go. Kubernetes required.
candidate.skills[3]:: Python|Go|Kubernetes
```

`key::value` for scalars; `key[n]` with a pipe-delimited run for lists. Per item this
costs roughly one delimiter instead of JSON's quoting and braces. `[n]` is computed
*after* truncation so it always describes what was actually serialized.

**Hostile-input handling.** The description is truncated to `_MAX_JD_CHARS` and its
newlines are collapsed. Without that, posting text could forge a `candidate.skills[...]`
header and inject fabricated skills into the request. This is a trust boundary: job
descriptions are attacker-influenced input.

**Evidence as output.** The model returns `evidence` — the skills it actually found —
alongside `matched_skills`, `missing_skills`, `explanation` and `confidence`. The field
is carried through `MatchExplanation` → graph state → `MatchedJobOut` so a reviewer can
audit a conclusion without re-reading the posting.

---

## 6. The company scraper

### 6.1 URL discovery

Guessing `/careers` is wrong more often than right. `_fetch_first_career_page()`
probes candidates concurrently and returns the first page **that yields job cards**:

```python
for url, html in zip(candidates, pages):
    if isinstance(html, str) and self._extract_job_cards(html):
        return html, url
```

Judging by extracted cards rather than a keyword heuristic matters: a generic landing
page mentions "career" and "join us" while listing zero roles. Paths are ordered —
`COMPANY_CAREER_PAGE_PATTERNS` first (operator intent), then built-in defaults — and
capped by `COMPANY_SCRAPER_MAX_URL_PROBES`.

### 6.2 Politeness model

| Concern | Mechanism |
|---|---|
| Hammering one site | `asyncio.Semaphore` **per hostname**, not global |
| Synchronised retries | exponential backoff + random jitter |
| Wasted requests | retry only on 429/5xx and transport errors; 4xx fails fast |
| Re-scraping unchanged pages | TTL cache (`COMPANY_SCRAPER_CACHE_TTL_SECONDS`) |
| Connection churn | one `httpx.AsyncClient` per scraper, reused across requests |
| Anonymity | configurable `User-Agent` identifying the project |

### 6.3 Extraction rules

- **Company** — read from a labelled element. If absent, fall back to the *requested*
  company name. Never fall back to the card text: that value flows into
  `content_hash`, so a wrong company silently breaks dedup.
- **Location** — labelled element, then parenthesised form (rejecting employment
  noise like `(Full-time)`), then a `City, Region` regex against the **original**
  casing. Casing matters: an uppercase-initial pattern run against a lowercased string
  can never match.
- **Apply URL** — only links *inside* the card. Walking up to a sibling card's anchor
  hands one posting another posting's URL, which then collides on `url_hash`. When no
  link exists, a stable `slug-index` URL is synthesised so two roles on the same page
  stay distinct.
- **Employment type** — emitted as the **raw** keyword so `normalize.py` maps it.

### 6.4 Client lifecycle

`CompanyScraper` owns an `httpx.AsyncClient`, so it is an async context manager:

```python
async with CompanyScraper() as scraper:
    postings = await scraper.scrape_company_jobs("Acme Corp")
```

The module-level `scrape_company_jobs()` wrapper does this for you. If you construct a
scraper directly and do not close it, connections leak until GC — a real problem in a
long-lived worker.

---

## 7. Extension points

| To add… | Do this |
|---|---|
| A job source | Subclass the `JobSourceAdapter` Protocol in `job_api_adapter.py`; register it in `get_adapter()` behind its credential check. |
| An ATS layout | Add the path to `COMPANY_CAREER_PAGE_PATTERNS`. No code change. |
| A hard filter | Add to `filters.py` and to the `SearchRequest` schema. |
| An LLM provider | Implement `LLMProvider` in `model_router.py`, add to `_get_provider()` and the fallback chain. |
| A table | Add to `models/`, re-export from `models/__init__.py`. `create_all()` creates it; an existing environment also needs a reviewed `CREATE TABLE`. |
| A response field | Add to `schemas/job.py`. It propagates to the OpenAPI schema automatically. |

---

## 8. Things that look wrong but are not

- **`get_settings()` is called at module scope in nine modules.** `Settings` is cheap
  and `lru_cache`d; this is the standard env-var pattern. Tests call
  `get_settings.cache_clear()` to change configuration mid-session.
- **`main.py` imports routes' dependencies inside the handler.** Deliberate: it keeps
  `import backend.main` cheap and avoids import cycles between the app and the graph.
- **No DDL at startup.** The lifespan creates no tables, so an environment cannot
  drift silently. Run
  `python -c "from backend.core.db import create_all; create_all()"` as a deploy step.
  It creates missing tables and missing indexes; widening an existing column is
  still a manual reviewed `ALTER`.
- **`_CompanyScraperAdapter` is defined after `get_adapter()` references it.** Fine —
  the name resolves at call time.
- **MySQL only.** `resolved_database_url` raises rather than falling back. A silent
  SQLite fallback would diverge from the production DDL.