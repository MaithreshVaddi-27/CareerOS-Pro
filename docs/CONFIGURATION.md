# Configuration

Every setting is an environment variable read by `backend/core/config.py` through
`pydantic-settings`. Copy [`.env.example`](../.env.example) to `.env` and edit it;
that file carries inline comments for each value.

```bash
cp .env.example .env
```

---

## Required

The app **refuses to start** without these. There is deliberately no default.

| Variable | Why |
|---|---|
| `MYSQL_PASSWORD` | Database credential. `resolved_database_url` raises without it. |
| `API_KEY` | Enables the `X-API-Key` gate. Unset means the API is **open**. |

---

## Core

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `development` | `development` selects in-memory LangGraph checkpoints. Anything else selects MySQL checkpoints. |
| `DATABASE_URL` | *(empty)* | Overrides the component-based MySQL URL. Leave empty to build it from the parts below. |
| `MYSQL_HOST` | `mysql` | Docker service name by default. |
| `MYSQL_PORT` | `3306` | |
| `MYSQL_DATABASE` | `careeros` | |
| `MYSQL_USER` | `careeros` | |

MySQL is the only supported database. `resolved_database_url` raises rather than
falling back, because a silent SQLite fallback would diverge from production DDL.

### Secrets and URLs

The password is URL-encoded when the connection string is assembled, so `@` and `:` in a
password are safe.

---

## Security and networking

| Variable | Default | Notes |
|---|---|---|
| `API_KEY` | *(required)* | Sent as `X-API-Key`. |
| `RATE_LIMIT_PER_MINUTE` | `60` | `0` or negative disables limiting. `< 10` and `> 1000` log a startup warning. |
| `REDIS_URL` | *(empty)* | Shared rate-limit counters. Empty = per-process counters. |
| `ALLOWED_ORIGINS` | `http://localhost:5173,http://localhost:3000,…` | Comma-separated CORS origins. |

### Rate limiting across workers

`REDIS_URL` empty means counters live in process memory. That is correct for a
single worker and wrong for several — each worker counts independently, so N workers
enforce N × `RATE_LIMIT_PER_MINUTE`.

```dotenv
REDIS_URL=redis://redis:6379/0
```

The bundled Redis is behind a compose profile so it stays optional:

```bash
docker compose --profile ratelimit up -d redis
```

`/health` reports which backend is live and marks the check `degraded` when
production is running on in-process counters.

If Redis is set but unreachable, the limiter **fails open** — requests are served and
an error is logged — rather than returning 500 on every call. The in-memory counter
still applies as a backstop.

Related: without proxy headers, every request looks like it came from the proxy, so
all clients share one bucket. See [DEPLOYMENT.md](DEPLOYMENT.md#behind-a-reverse-proxy).

### Checkpoint durability

| Variable | Default | Notes |
|---|---|---|
| `CHECKPOINT_STRICT` | `false` | `true` refuses to start when MySQL checkpointing fails, instead of silently degrading to in-memory. |

MySQL 9.x cannot run `AIOMySQLSaver.setup()` (it disallows MD5 in generated columns).
The app degrades to in-memory checkpoints and **says so on `/health`**, but the
checkpointer still looks healthy from every other angle. Set this `true` in production
so the failure is a refusal to start rather than silent data loss.

---

## Job sources

| Variable | Default | Notes |
|---|---|---|
| `JOB_API_MODE` | `mock` | `mock` = deterministic fixtures, no network. `live` = real adapters. |
| `JOB_SOURCES` | `jsearch,adzuna,remotive,remoteok,arbeitnow,company` | Which adapters to enable. |

| Credential | Enables |
|---|---|
| `JSEARCH_API_KEY` | `jsearch` |
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | `adzuna` |
| `ADZUNA_COUNTRY` | market code — `gb`, `us`, `in`, … |
| *(none)* | `remotive`, `remoteok`, `arbeitnow`, `company` |

An adapter is constructed **only** when its credential is present, so an empty key
disables that source rather than causing a runtime error. In `live` mode, if the
requested set yields zero configured adapters the app raises with a message naming the
variables to set. Naming a source in `JOB_SOURCES` without its credential is silently
skipped — the free sources still work, so this is rarely fatal.

`SearchRequest.sources` overrides `JOB_SOURCES` for a single request.

---

## LLM providers

**Gemini is the only LLM provider.** The llama.cpp, NVIDIA NIM, and OpenRouter
integrations were removed — their settings and packages are gone, not deprecated.

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER_MODE` | `auto` | `auto` uses Gemini when `GEMINI_API_KEY` is set. `gemini` requires it. `none` disables LLM explanations. |
| `GEMINI_API_KEY` | *(empty)* | From https://aistudio.google.com/app/apikey. |
| `GEMINI_MODEL` | `gemini-flash-latest` | Rolling alias, not a pinned version — Google retires dated model IDs on a rolling schedule, so a pinned string eventually 404s. |

No fallback chain remains. If the Gemini call fails, the explanation is omitted and
the failure is logged rather than silently swallowed.

With no provider configured the pipeline still runs; explanations come back empty with
`confidence: 0.0`. See [API.md](API.md#behaviour-without-any-llm-configured).

Per-request overrides: `MatchRequest.llm_provider` and `MatchRequest.model_name`.

---

## Storage and embeddings

| Variable | Default | Notes |
|---|---|---|
| `QDRANT_URL` | *(empty)* | Empty selects an embedded/local Qdrant instance. |
| `FIRECRAWL_API_KEY` | *(empty)* | Enables `POST /jobs/{job_id}/verify`. |

`scikit-learn` supplies the local embedding fallback when no provider is configured.

---

## Company scraper

The most commonly tuned block. Scraping is free — no credential involved.

| Variable | Default | Notes |
|---|---|---|
| `COMPANY_CAREER_PAGE_PATTERNS` | *(empty)* | Comma-separated paths probed under the company domain, **highest priority first**. Add ATS-specific paths here instead of editing code. |
| `COMPANY_SCRAPER_MAX_URL_PROBES` | `4` | Upper bound on paths probed per company. |
| `COMPANY_SCRAPER_CACHE_SIZE` | `100` | Cached HTML pages held in memory. FIFO eviction. |
| `COMPANY_SCRAPER_CACHE_TTL_SECONDS` | `900` | Cache lifetime. Prevents returning a stale job list. `0` disables expiry. |
| `COMPANY_SCRAPER_MAX_CONCURRENCY` | `5` | Concurrent requests **per hostname**. |
| `COMPANY_SCRAPER_JITTER_MAX` | `0.5` | Max random jitter added to retry backoff. |
| `COMPANY_SCRAPER_TIMEOUT_SECONDS` | `20` | Per-request HTTP timeout. |
| `COMPANY_SCRAPER_MAX_RETRIES` | `2` | Retries *after* the first attempt. |
| `COMPANY_SCRAPER_USER_AGENT` | `CareerOS/1.0 (+…/CareerOS-Pro)` | Identify yourself to the sites you scrape. |

### How URL discovery behaves

`COMPANY_CAREER_PAGE_PATTERNS` entries are tried **before** the built-in defaults, and
deduped against them. The first probed page that yields actual job cards wins — not
the first that returns `200`.

```dotenv
# Teach it Workday and SmartRecruiters without touching code:
COMPANY_CAREER_PAGE_PATTERNS=/careers/jobs,/search/careers,/jobs
```

```bash
# Confirm what a company resolves to before wiring it up:
python -m backend.cli "Acme Corp" --max-concurrency 1
```

### Retry policy

| Response | Behaviour |
|---|---|
| `200` | Cached and returned. |
| `429`, `500`, `502`, `503` | Retried with exponential backoff + jitter. |
| Transport error | Retried with backoff + jitter. |
| Any other `4xx` | **Fails immediately** — a `403` or `404` will not become a `200` on retry. |

### Targeting a company

The company adapter only fires when the query names a company:

- A **proper noun** in the query — `Stripe engineer` → scrapes `stripe.com`
- An **explicit prefix** — `company:stripe` (the escape hatch for lowercase names)

A query naming no company returns no company results. It does **not** substitute a
default shortlist.

---

## Frontend

| Variable | Where | Notes |
|---|---|---|
| `VITE_API_BASE` | `frontend/.env` | Backend base URL. Defaults to the local API. |

---

## Job retention (no environment variables)

Retention is configured on the command line, not through `.env`, so there is no
restart-required knob to get wrong.

```bash
python -m backend.retention --days 90            # dry run: reports the count only
python -m backend.retention --days 90 --yes      # deletes, in bounded batches
```

| Flag | Default | Notes |
|---|---|---|
| `--days` | `90` | Inactive for at least this long. |
| `--batch-size` | `1000` | Max rows per run, so one run cannot take a long lock. |
| `--yes` | off | Without it the run deletes nothing. |

Rows are keyed on `updated_at`, which the `is_active` flip itself bumps — retention
measures staleness, not age, so a freshly-refreshed posting is never expired early.
Rows still active are never eligible at any age.

---

## Interaction notes

**Settings are cached.** `get_settings()` is `@lru_cache`d and called at module scope
in nine modules, so the environment is read once per process. Tests that change
configuration must call `get_settings.cache_clear()`.

**List-typed settings are comma-separated strings, not JSON.** `JOB_SOURCES`,
`ALLOWED_ORIGINS` and `COMPANY_CAREER_PAGE_PATTERNS` are all `str` fields split on
commas. `pydantic-settings` only parses JSON for real container types, so typing one
as `list[str]` would require `["a","b"]` in `.env` and silently break the common case.

**Changing a `String(n)` length.** There is no migration tool. Change the model in
`backend/models/job.py`, then apply the matching statement to each environment:

```sql
ALTER TABLE jobs MODIFY source_id VARCHAR(512);
```

`create_all()` creates missing tables and missing indexes, but will not alter
existing columns, so a live database needs this by hand. See [DEPLOYMENT.md](DEPLOYMENT.md#troubleshooting).

---

## Production checklist

- [ ] `APP_ENV` is not `development` (enables durable checkpoints)
- [ ] `API_KEY` is a long random value
- [ ] `RATE_LIMIT_PER_MINUTE` is set deliberately
- [ ] `ALLOWED_ORIGINS` lists only real frontend origins
- [ ] MySQL is 8.x, not 9.x — or `CHECKPOINT_STRICT=true` so a checkpoint
      failure is a refusal to start rather than silent data loss
- [ ] `.env` is not committed — it is gitignored (`.env.*` is ignored, so
      `.env.production` and `.env.staging` are covered too; `.env.example` is not)
- [ ] `COMPANY_SCRAPER_USER_AGENT` identifies you with a contact URL
- [ ] `python -c "from backend.core.db import create_all; create_all()"` has been run against the deployed database
- [ ] A recurring `python -m backend.retention --days N --yes` job is scheduled, so
      `is_active = false` rows do not accumulate without bound