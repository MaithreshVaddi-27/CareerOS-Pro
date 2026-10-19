<div align="center">

# CareerOS

**A self-hosted job-intelligence platform: aggregate postings from many sources, hard-filter them deterministically, rank them by hybrid keyword + semantic score, and explain every match with auditable LLM evidence.**

[![CI](https://github.com/MaithreshVaddi-27/CareerOS-Pro/actions/workflows/ci.yml/badge.svg)](https://github.com/MaithreshVaddi-27/CareerOS-Pro/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## Table of contents

- [Why it exists](#why-it-exists)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [API reference](#api-reference)
- [Architecture](#architecture)
- [Job sources](#job-sources)
- [The LLM layer](#the-llm-layer)
- [The company scraper](#the-company-scraper)
- [Development](#development)
- [Documentation](#documentation)
- [Project status](#project-status)
- [License](#license)

---

## Why it exists

Most job-search tooling makes you trust a black box: it hands you a ranked list and you
cannot tell *why* anything is on it, or whether the "skills" it matched are actually in the
job description.

CareerOS is built the other way around:

| Principle | How it is enforced in code |
|---|---|
| **Deterministic where it matters** | Filtering, normalization, dedup and ranking are pure Python. No LLM decides what you are eligible for. |
| **Never invent data** | A field that cannot be read from the source stays `unknown`. Nothing is guessed to fill a gap. |
| **Every match is auditable** | `/jobs/match` returns `evidence` — the skills quoted from the posting — alongside the model's conclusion. |
| **Grounded generation** | The match prompt is CRAFT-structured with an explicit grounding pass and a "subset of candidate skills" constraint. |
| **Local-first, credential-optional** | Runs with zero API keys in `mock` mode. Every integration degrades to "no results", never to a crash. |

---

## How it works

```
        ┌──────────────────────┐
        │  Job source adapters │   JSearch · Adzuna · Remotive · RemoteOK
        │  (concurrent, fanned)│   Arbeitnow · company career pages
        └──────────┬───────────┘
                   │  RawJobPosting  (raw source text, never pre-normalized)
                   ▼
        ┌──────────────────────┐
        │     normalize.py     │   deterministic parse → NormalizedJob
        │                      │   unknown stays unknown
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────┐
        │      dedup.py         │   url_hash (unique) + content_hash
        └──────────┬───────────┘
                   │ created only → index
                   ▼
        ┌──────────────────────┐
        │   vector_store.py    │   Qdrant (embedded) or in-process fallback
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────┐
        │      filters.py      │   HARD filters — employment, level, remote,
        │                      │   salary, age, location
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────┐
        │    reranker.py       │   hybrid = weighted(keyword, semantic)
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────┐
│    model_router.py   │   grounded explanation via Gemini
         │                      │   (the only LLM provider)
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────┐
        │   human_review       │   LangGraph interrupt — nothing is
        │   (interrupt)        │   auto-applied, ever
        └──────────────────────┘
```

The whole match path is a [LangGraph](https://langchain-ai.github.io/langgraph/) state
machine (`backend/graph.py`). It is **linear and unconditional** — the LLM never overrides
a hard filter, and the graph pauses for human review before anything is marked
ready-to-apply.

---

## Quick start

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.12 | Backend runtime |
| Node.js | 20+ | Frontend build |
| MySQL | 8.0+ | System of record for every deployment. |

### 1. Configure

```bash
git clone https://github.com/MaithreshVaddi-27/CareerOS-Pro.git
cd CareerOS-Pro
cp .env.example .env
```

Two values have **no defaults** and the app will refuse to start without them:

```dotenv
MYSQL_PASSWORD=your-password
API_KEY=choose-a-long-random-string
```

> MySQL 8.x only. MySQL 9.x disallows MD5 in generated columns, which breaks the
> LangGraph checkpoint tables. The app falls back to in-memory checkpoints and reports
> it as `degraded` on `/health`; set `CHECKPOINT_STRICT=true` in production to make that
> a refusal to start instead.

### 2. Backend

```bash
python -m venv backend/venv
source backend/venv/bin/activate        # Windows: backend\venv\Scripts\activate
pip install -r requirements.txt

# MySQL 8.x must already be running and reachable on MYSQL_HOST.
# See docs/DEPLOYMENT.md § Local development for the create-user SQL.
python -c "from backend.core.db import create_all; create_all()"  # create the schema
python run.py                            # → http://127.0.0.1:8000
```

> The app runs **no DDL at startup** — create tables from the SQLAlchemy models
> (see [DEPLOYMENT.md](docs/DEPLOYMENT.md)).

Interactive API docs: <http://127.0.0.1:8000/docs>

### 3. Frontend

```bash
cd frontend
npm install
npm run dev                              # → http://127.0.0.1:5173
```

Set `VITE_API_BASE=http://127.0.0.1:8000` in `frontend/.env`.

### Production

Native host deployment — no container runtime. `deploy/` holds a hardened systemd
unit and a ready nginx site config; see [`deploy/README.md`](deploy/README.md).

```bash
sudo cp deploy/careeros-api.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now careeros-api
```

Qdrant needs no install: with `QDRANT_URL` empty the client opens an embedded
on-disk store at `./qdrant_local`. Redis is optional and only needed for shared
rate-limit counters when running more than one worker.

### Verify it works

```bash
curl -H "X-API-Key: $API_KEY" \
     -H 'Content-Type: application/json' \
     -d '{"query":"python","candidate_skills":["Python","FastAPI"]}' \
     http://127.0.0.1:8000/jobs/match
```

You should get a ranked list with `evidence`, `matched_skills`, `missing_skills`
and a `confidence` per job. With no API keys configured it still runs — every
provider returns a graceful "no LLM available" explanation rather than failing.

### Housekeeping — pruning inactive jobs

When a posting disappears upstream its row is marked `is_active = false` rather
than deleted, so `jobs` only ever grows. Prune those rows on a schedule:

```bash
# Report only — this is the default and changes nothing.
python -m backend.retention --days 90

# Delete, in bounded batches so a single run cannot take a long lock.
python -m backend.retention --days 90 --yes
```

Retention is keyed on `updated_at`, which the `is_active` flip itself bumps — so it
measures staleness, not age, and a freshly-refreshed posting is never expired early.
Each run deletes at most `--batch-size` rows (default 1000); re-run until it reports
nothing left. Schedule it with cron, e.g. daily at 03:17:

```cron
17 3 * * * cd /srv/careeros && backend/venv/bin/python -m backend.retention --days 90 --yes
```

---

## Configuration

Every variable is documented inline in [`.env.example`](.env.example). The
scraper-related knobs are summarised here because they are the ones most often
tuned:

| Variable | Default | Purpose |
|---|---|---|
| `COMPANY_CAREER_PAGE_PATTERNS` | *(empty)* | Extra career paths to probe, highest priority first. Use it to teach a new ATS layout **without touching code**. |
| `COMPANY_SCRAPER_MAX_URL_PROBES` | `4` | How many paths are probed per company. |
| `COMPANY_SCRAPER_CACHE_SIZE` | `100` | Cached HTML pages held in memory. |
| `COMPANY_SCRAPER_CACHE_TTL_SECONDS` | `900` | Cache lifetime. `0` disables expiry. |
| `COMPANY_SCRAPER_MAX_CONCURRENCY` | `5` | Concurrent requests **per domain** — keeps us polite. |
| `COMPANY_SCRAPER_TIMEOUT_SECONDS` | `20` | Per-request HTTP timeout. |
| `COMPANY_SCRAPER_MAX_RETRIES` | `2` | Retries *after* the first attempt. 4xx never retries. |
| `COMPANY_SCRAPER_USER_AGENT` | `CareerOS/1.0 …` | Identify yourself to the sites you scrape. |

Full reference: **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.

---

## API reference

All routes except `/health*` require the `X-API-Key` header.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | — | Component health: DB, rate limiter, checkpointer, embeddings, vector store. |
| `GET` | `/health/ready` | — | Readiness probe. `200` when servable, `503` otherwise. |
| `GET` | `/agents/health` | key | Per-agent call counts, latency and failure rate. |
| `POST` | `/jobs/search` | key | Fan out to all sources, normalize, dedup, hard-filter. |
| `GET` | `/jobs/{job_id}` | key | Fetch one posting. |
| `POST` | `/jobs/match` | key | Full pipeline: search → rank → grounded explanation. |
| `POST` | `/jobs/{job_id}/verify` | key | Confirm a posting is still live. Requires `FIRECRAWL_API_KEY`. |

<details>
<summary>Request / response bodies</summary>

**`POST /jobs/search`**

```jsonc
// request
{
  "query": "python",
  "location": "Berlin",
  "employment_type": "full_time",   // optional
  "experience_level": "senior",     // optional
  "remote_only": false,
  "min_salary": 0,
  "posted_within_days": 30,
  "sources": ["jsearch", "company_scraper"], // optional; defaults to JOB_SOURCES
  "limit": 50,                       // 1..200, default 50
  "offset": 0                        // default 0
}
// → [ JobOut, … ]   (at most `limit` rows)
```

`sources` filters on the **stored** `Job.source` value, not on the `JOB_SOURCES`
enable name. The two differ for exactly one source: the career-page scraper is enabled
by `company` in `JOB_SOURCES` but writes rows tagged `company_scraper`. Every other
source uses the same name in both places.

Filtering happens in SQL against indexed columns, and `/jobs/match` no longer loads
the whole table. `limit`/`offset` cap how many rows come back; the response shape is
unchanged, so an empty page just means you paged past the end.

**`POST /jobs/match`**

```jsonc
// request
{
  "query": "python",
  "candidate_skills": ["Python", "FastAPI", "PostgreSQL"],
  "top_k": 10,                 // page size, 1..100
  "offset": 0,                 // default 0
  "llm_provider": "auto"       // auto | gemini | none
}
// → [ MatchedJobOut, … ]
```

`MatchedJobOut` is where the grounding guarantee is visible:

```jsonc
{
  "job": { /* JobOut */ },
  "keyword_score": 0.71,
  "semantic_score": 0.84,
  "hybrid_score":  0.79,
  "evidence": "python|fastapi|kubernetes",   // quoted from the posting
  "matched_skills": ["Python", "FastAPI"],
  "missing_skills": ["Kubernetes"],
  "explanation": "Strong overlap on backend Python; no container experience stated.",
  "confidence": 0.8
}
```

</details>

Full reference with error shapes: **[docs/API.md](docs/API.md)**.

---

## Architecture

```
backend/
├── core/            cross-cutting concerns
│   ├── config.py    env-driven Settings, lru_cached
│   ├── db.py        engine, SessionLocal, Base, create_all()
│   ├── middleware.py  request-id, security headers, rate limit, API-key auth
│   └── monitoring.py  AgentMonitor — per-agent latency/failure stats
├── models/          SQLAlchemy ORM (Job)
├── schemas/         Pydantic request/response contracts
├── services/        business logic — all of it here
│   ├── sourcing/    everything that brings job data in from outside
│   │   ├── job_api_adapter.py  source adapters + fan-out
│   │   ├── company_scraper.py  career-page scraping
│   │   ├── normalize.py        deterministic parsing
│   │   ├── dedup.py            upsert by hash
│   │   └── verification.py     liveness check
│   └── matching/    everything that turns rows into a ranked answer
│       ├── filters.py          hard filters (SQL prefilter + authoritative Python pass)
│       ├── reranker.py         hybrid scoring
│       ├── model_router.py     LLM providers + grounded prompting
│       ├── embeddings.py       embedding providers
│       └── vector_store.py     Qdrant + fallback
├── graph.py         LangGraph orchestration (no business logic)
├── main.py          FastAPI app + routes
├── cli.py           scrape a single company to JSON
├── retention.py     bounded pruning of inactive jobs
└── run.py           entrypoint
```

**The one rule that keeps this codebase navigable:** `main.py` and `graph.py` contain
no business logic. `graph.py` in particular is pure orchestration — every node just
calls a service function. If you find yourself writing a `if` about job data in
`graph.py`, it belongs in `services/`.

**The `services/` split is the second rule: `sourcing` versus `matching`.** If the
module talks to an external system to learn about jobs — or judges whether a posting
we already hold is still live — it is `sourcing`. If it turns rows we already have
into a ranked, explained answer, it is `matching`. Nothing in `matching` may fetch a
job; that boundary is what stops the graph re-acquiring the same posting on every
request. The two `__init__.py` files state the rule in full.

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the data flow, the
extension points, and the design decisions behind the layout.

---

## Job sources

Enable via the comma-separated `JOB_SOURCES` variable. All fan out concurrently;
a failing source degrades to an empty result and never fails the request.

| Source | Credential | Notes |
|---|---|---|
| `jsearch` | `JSEARCH_API_KEY` | Broad aggregator, best coverage. |
| `adzuna` | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | Strong in UK/EU markets. |
| `remotive` | none | Remote-first, generous limits. |
| `remoteok` | none | Remote-only, no key needed. |
| `arbeitnow` | none | EU-focused, no key needed. |
| `company` | none | Scrapes the company's own career pages. Rows are tagged `company_scraper`. |

Set `JOB_API_MODE=mock` for offline development — returns a deterministic fixture set
and performs no network I/O.

---

## The LLM layer

**Gemini is the only LLM provider.** The llama.cpp, NVIDIA NIM, and OpenRouter
integrations have been removed — their settings and packages are gone. Set
`GEMINI_API_KEY` and explanations work; leave it unset and the pipeline still
runs, returning `confidence: 0.0` with no explanation. You can pin the provider per
request with `llm_provider`, or globally with `LLM_PROVIDER_MODE`.

**The prompt.** `backend/services/matching/model_router.py` builds one prompt per job:

- **CRAFT structure** — Role / Context / Action / Format / Tone. The Role and the
  grounding contract exist to suppress the one failure that matters: inventing skills.
- **TOON serialization** — the request ships as `key::value` scalars and pipe-delimited
  lists (`candidate.skills[12]:: python|docker|kubernetes`) instead of JSON. For a
  twelve-item list that is one delimiter per item rather than per-item quoting and braces.
- **Bounded input** — the description is truncated to 1200 characters and newlines are
  collapsed, so posting text cannot forge structure in the request block.
- **A short ordered grounding pass** before answering: find skills you can point at,
  split them, re-check every entry against its source span, drop what you cannot source.
- **A worked example**, because small local models drift on JSON shape without one.

The model must return `matched_skills` as a strict subset of your declared skills.
`confidence < 0.5` is expected on a thin posting — that is the router escalating, not
failing.

---

## The company scraper

Free, credential-free, and deliberately conservative.

**Finding the page.** Guessing `/careers` is wrong more often than it is right, so the
scraper probes a list of candidate paths concurrently and takes the first page that
actually yields job cards — not the first page that returns 200. A generic landing page
that mentions "career" does not end the search. Add ATS-specific paths via
`COMPANY_CAREER_PAGE_PATTERNS` rather than editing code.

**Politeness.** Concurrency is capped **per domain**, retries use exponential backoff
with jitter, 429/5xx retry while 4xx fails immediately (a 403 will not become a 200),
responses are cached with a TTL so repeat queries do not re-hit the site, and the
`User-Agent` identifies the project.

**Extraction.** Company, location, employment type and apply URL are read from labelled
elements when the page provides them. What cannot be read stays empty so
`normalize.py` can mark it `unknown` — the scraper never guesses.

```bash
# Scrape one company to JSON
python -m backend.cli "Acme Corp" --location Berlin --max-concurrency 2
```

---

## Development

```bash
source backend/venv/bin/activate
pip install "ruff==0.17.1"

ruff check backend/ tests/       # lint
ruff check backend/ tests/ --fix
python -m pytest -v                      # tests

```

Ruff is **pinned in CI on purpose**. Its default rule set changed drastically in 0.17,
so an unpinned `pip install ruff` makes CI pass or fail based on whatever the runner
resolved that day. The rule set is pinned in `pyproject.toml`; pytest config lives in
`pytest.ini` (which takes precedence, so it is not duplicated).

The frontend has no test suite — `npm run build` is the available signal.

Workflow, conventions and the review checklist: **[docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)**.

---

## Documentation

| Document | What it covers |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module map, data flow, design decisions, extension points |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Local and production deployment; MySQL caveats |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every environment variable, defaults and interactions |
| [docs/API.md](docs/API.md) | Endpoint reference, request/response shapes, error semantics |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | Dev workflow, testing conventions, PR checklist |

---

## Project status

Active development. Honest summary of where it stands:

**Working and tested** — multi-source aggregation, deterministic normalization and
dedup, SQL-side hard filtering over indexed columns, bounded candidate retrieval,
hybrid ranking, grounded LLM explanation, career-page scraping, liveness verification,
health and agent monitoring, CI.

**Known limitations** — `location` is matched with SQL `LIKE`, so short codes like
`in` or `ny` can over-match in the database pass (`LIKE '%in%'` also hits "kingdom");
the authoritative Python pass then rejects those rows, so results stay correct at a
small cost in rows read. The vector store falls back to a local in-process
implementation; the human-review interrupt is wired but the resume path is not yet
exposed over HTTP. Inactive rows accumulate between retention runs. Timestamps are
stored as naive `DateTime` in MySQL, so a deployment spanning a DST boundary can
displace "posted within N days" by an hour.

---

## License

[MIT](LICENSE) © CareerOS contributors.