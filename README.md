# CareerOS‑Pro

AI‑powered career intelligence platform that **autonomously discovers, filters, ranks, and explains** job and internship opportunities. It aggregates listings from multiple free and paid sources, normalizes them deterministically, deduplicates, applies hard eligibility filters, and uses an LLM routing pipeline to explain matches.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Supported Platforms](#supported-platforms)
- [Setup Order of Execution](#setup-order-of-execution)
- [Quick Start](#quick-start)
- [Backend Setup](#backend-setup)
- [Frontend Setup](#frontend-setup)
- [Docker Deployment](#docker-deployment)
- [Configuration](#configuration)
- [CLI Usage](#cli-usage)
- [Testing](#testing)
- [Monitoring](#monitoring)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- **Multi‑source aggregation** — JSearch (RapidAPI), Adzuna, Remotive, RemoteOK, Arbeitnow, plus a free company‑career‑page scraper (BeautifulSoup4).
- **Deterministic normalization** — no LLM in the parsing path. Location/remote classification, employment‑type mapping, experience‑level classification (`intern`/`fresher`/`no_experience`/`entry`/`mid`/`senior`/`unknown`), and salary parsing with currency conversion.
- **Two‑stage deduplication** — exact `url_hash` first, then `content_hash` fallback.
- **Hard eligibility filters** — pure logic, LLM never overrides (employment type, experience, remote‑only, location alias matching, query string, min salary, posted‑within‑days).
- **LLM routing & explanation** — LangGraph state machine with multi‑provider fallback (LlamaCpp → NVIDIA NIM → OpenRouter → Gemini).
- **Two‑stage verification** — cheap HTTP HEAD check, then Firecrawl content scrape. Never invents a result.
- **Polite company scraper** — per‑domain concurrency control (semaphore), exponential backoff with jitter, in‑memory HTML cache. Runs as the `company` job source alongside the API adapters — free, no API key, uses BeautifulSoup4 (already in `requirements.txt`).
- **Agent health monitoring** — every LLM provider and job‑source adapter is a self‑contained agent that can fail independently without taking the system down. `GET /agents/health` reports per‑agent call counts, failure rate, latency, and status (`healthy`/`degraded`/`down`) — see [`backend/core/monitoring.py`](backend/core/monitoring.py).

---

## Architecture

```
Frontend (React 19 + Vite + Framer Motion)
        │  REST / CORS
        ▼
Backend (FastAPI + Granian)
  ├─ core/config.py               → pydantic_settings, env‑driven
  ├─ core/db.py                   → engine/session + startup schema sync
  ├─ core/middleware.py           → request-id, API-key auth, rate limiting
  ├─ models/job.py                → MySQL Job model (source of truth)
  ├─ schemas/job.py                → pydantic request/response schemas
  ├─ services/job_api_adapter.py   → MultiSourceAdapter (JSearch, Adzuna, Remotive, RemoteOK, Arbeitnow)
  ├─ services/company_scraper.py    → BeautifulSoup4 career‑page scraper
  ├─ services/normalize.py          → deterministic field normalization
  ├─ services/dedup.py              → url_hash + content_hash dedup
  ├─ services/filters.py            → hard eligibility filters
  ├─ services/verification.py       → HEAD + Firecrawl verify
  ├─ graph.py                       → LangGraph match/explain pipeline
  └─ main.py                        → FastAPI app + routes + lifespan
        │
        ├─ MySQL (canonical job store)
        └─ Qdrant (vector retrieval for semantic ranking)
```

**Data flow:** Sources → Normalize → Dedup → Filters → DB → (optional) LLM match/verify → Frontend.

---

## Project Structure

```
CareerOS-Pro/
├── backend/
│   ├── main.py            # FastAPI app, routes, startup/shutdown lifespan
│   ├── run.py              # process entrypoint (uvicorn/granian launcher)
│   ├── cli.py               # `python -m backend.cli` company-scraper CLI
│   ├── graph.py              # LangGraph match/explain state machine
│   ├── core/                  # cross-cutting infra — nothing job-domain-specific here
│   │   ├── config.py            # env-driven settings (pydantic_settings)
│   │   ├── db.py                 # SQLAlchemy engine/session + sync_schema()
│   │   └── middleware.py         # request-id, API-key auth, rate limiting
│   ├── models/                # SQLAlchemy ORM models
│   │   └── job.py
│   ├── schemas/                # pydantic request/response contracts
│   │   └── job.py
│   └── services/               # business logic, one concern per file
│       ├── job_api_adapter.py
│       ├── company_scraper.py
│       ├── normalize.py
│       ├── dedup.py
│       ├── filters.py
│       ├── verification.py
│       ├── model_router.py
│       └── vector_store.py
├── frontend/
│   ├── src/
│   │   ├── components/{jobs,layout,ui}/
│   │   ├── services/api.js       # backend REST client
│   │   ├── hooks/
│   │   └── utils/
│   └── vite.config.js
├── tests/                    # pytest, mirrors backend/ by concern not by path
├── .env.example
├── docker-compose.yml
├── Dockerfile
└── README.md
```

`backend/models/__init__.py` and `backend/schemas/__init__.py` re-export their single
module (`Job`, and the request/response classes) so existing code doing
`from backend.models import Job` / `from backend.schemas import SearchRequest` keeps working —
only `backend.config`/`backend.db`/`backend.middleware` importers need the new `backend.core.`
prefix. `models/` and `schemas/` are single-file packages today (one domain: jobs) — split further
into `models/job.py` + `models/user.py` etc. only once a second domain actually shows up; don't
pre-build structure for entities that don't exist yet.

---

## Supported Platforms

CareerOS‑Pro runs on **macOS**, **Linux**, and **Windows**. Commands below are shown per platform where they differ.

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python      | 3.11+   | Backend runtime |
| Node.js     | 20+     | Frontend toolchain |
| MySQL       | 8.0+    | Canonical job store |
| Docker      | 24+     | Optional, for containerized deploy |
| Git         | any modern | For cloning |

**Shell conventions used in this guide**

| Platform | Shell | Notes |
|----------|-------|-------|
| macOS / Linux | Bash / Zsh | Commands run as‑written. |
| Windows (PowerShell) | `PS>` | Use `python` or `py` depending on install; paths use backslashes or quoted forward slashes. |
| Windows (WSL2) | Bash | Same as Linux — recommended for the smoothest experience. |
| Windows (CMD) | `C:\>` | Use `python` instead of `python3`; `venv\Scripts\activate.bat` instead of `source`. |

> **Tip (Windows):** If `python` is not on your PATH, use `py` (the Python launcher). For the backend venv on Windows, activate with `venv\Scripts\activate` (PowerShell) or `venv\Scripts\activate.bat` (CMD).

---

## Setup Order of Execution

Run these steps **in order**. Later steps depend on earlier ones being complete.

1. **Clone the repository** — get the code locally.
2. **Install toolchains** — Python 3.11+ and Node 20+ must both be available.
3. **Provision the database** — start MySQL (native or via Docker) and ensure it is reachable. The backend builds the schema on first run.
4. **Create and configure `.env`** — copy `.env.example` to `.env` and fill in DB credentials and any API keys. This must exist before the backend starts.
5. **Install backend dependencies** — create a venv and install requirements.
6. **Install frontend dependencies** — `npm install` in the frontend directory.
7. **Start the backend** — runs the API on `http://localhost:8000`.
8. **Start the frontend** — runs the app on `http://localhost:5173`.
9. **(Optional) Run tests** — verify the setup with `pytest`.

Do **not** start the frontend before the backend is up, since the frontend calls the API at `http://localhost:8000` by default.

---

## Quick Start

### macOS / Linux (Bash / Zsh)

```bash
# Backend (terminal 1)
cd backend
python -m venv venv && source venv/bin/activate
pip install -r ../requirements.txt
cp ../.env.example ../.env   # then fill in DB + API keys
python run.py
# API:    http://localhost:8000
# Docs:   http://localhost:8000/docs

# Frontend (terminal 2)
cd frontend
npm install
npm run dev
# App:    http://localhost:5173
```

### Windows (PowerShell)

```powershell
# Backend (terminal 1)
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r ..\requirements.txt
Copy-Item ..\.env.example ..\.env   # then fill in DB + API keys
python run.py
# API:    http://localhost:8000
# Docs:   http://localhost:8000/docs

# Frontend (terminal 2)
cd frontend
npm install
npm run dev
# App:    http://localhost:5173
```

### Windows (WSL2 — Bash)

```bash
# Same as macOS / Linux. WSL2 shares the Windows filesystem but runs a real Linux shell.
# Backend (terminal 1)
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r ../requirements.txt
cp ../.env.example ../.env
python run.py

# Frontend (terminal 2)
cd frontend && npm install && npm run dev
```

> Requires Python 3.11+ and Node 20+ on all platforms.

---

## Backend Setup

### macOS / Linux

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r ../requirements.txt
cp ../.env.example ../.env   # then fill in DB + API keys
python run.py
```

### Windows (PowerShell)

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r ..\requirements.txt
Copy-Item ..\.env.example ..\.env
python run.py
```

### Windows (CMD)

```cmd
cd backend
python -m venv venv
venv\Scripts\activate.bat
pip install -r ..\requirements.txt
copy ..\.env.example ..\.env
python run.py
```

**Important Security Note**: `MYSQL_PASSWORD` has no default and must be set in your `.env` file — the backend won't start without it. `API_KEY` must also be *present* in `.env` (even as an empty value — that's the shipped default) but does **not** need to be filled in: empty means no auth required, which is fine for local development. Only set `API_KEY` to a real value if you're exposing the backend somewhere untrusted — and if you do, set `VITE_API_KEY` to the exact same value in `frontend/.env`, or every frontend request will fail with `401 Unauthorized`.

The backend expects a reachable MySQL instance. `DATABASE_URL` is constructed from `MYSQL_*` env vars (see `.env.example`). Qdrant is optional for semantic ranking but recommended.

The backend is powered by Uvicorn (ASGI server) for optimal performance and compatibility.

#### Starting MySQL (per platform)

**macOS (Homebrew):**
```bash
brew install mysql
brew services start mysql
```

**Linux (Debian/Ubuntu):**
```bash
sudo apt update && sudo apt install -y mysql-server
sudo service mysql start
```

**Windows (WSL2):**
```bash
sudo apt update && sudo apt install -y mysql-server
sudo service mysql start
```

**Any platform (Docker):**
```bash
docker run -d --name careeros-mysql \
  -e MYSQL_ROOT_PASSWORD=rootpass \
  -e MYSQL_DATABASE=careeros \
  -p 3306:3306 \
  mysql:8.0
```

---

## Frontend Setup

```bash
cd frontend
cp .env.example .env   # optional — default already points at http://localhost:8000
npm install
npm run dev
```

The frontend reads `VITE_API_BASE` (defaults to `http://localhost:8000`). CORS is pre‑configured for ports `5173` and `3000`. On Windows, the same commands apply in PowerShell, CMD, or WSL2.

---

## Docker Deployment

```bash
# Build and start full stack (backend + MySQL + Qdrant + frontend)
docker compose up -d

# Or backend image only
docker build -t careeros-pro .
docker run -p 8000:8000 -e APP_ENV=production careeros-pro
```

**Multi‑platform builds:** To build for a non‑native architecture (e.g., ARM on an x86 machine, or vice versa):

```bash
docker buildx create --use
docker buildx build --platform linux/amd64,linux/arm64 -t careeros-pro . --load
```

**Security note:** The `Dockerfile` does **not** copy `.env` files into the image. Mount secrets at runtime via `env_file` in `docker-compose.yml` or `-e` flags. `.env` is git‑ignored.

---

## Configuration

All configuration is environment‑driven via `backend/core/config.py` (pydantic_settings). Key groups:

| Group | Highlights |
|-------|-----------|
| Application | `APP_ENV` (development \| production) |
| Database | `MYSQL_*` components or explicit `DATABASE_URL` (MySQL only) |
| Job APIs | `JOB_API_MODE` (mock \| live), `JOB_SOURCES`, JSearch/Adzuna keys |
| AI/LLM | `LLM_PROVIDER_MODE`, per‑provider keys & models |
| Vector | `QDRANT_URL` |
| Verification | `FIRECRAWL_API_KEY` |
| Security | `API_KEY`, `RATE_LIMIT_PER_MINUTE` |

See [`.env.example`](.env.example) for the full template with inline docs.

### Company Scraper Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `company_scraper_max_concurrency` | `5` | Max concurrent requests **per domain**. |
| `company_scraper_jitter_max` | `0.5` | Max seconds of random jitter on retry backoff. |
| `company_scraper_cache_size` | `100` | Max cached HTML responses (in memory). |
| `company_career_page_patterns` | `[]` | Custom URL patterns for career pages. |

Enable via `JOB_SOURCES=company`.

---

## CLI Usage

```bash
# macOS / Linux
python -m backend.cli <company_name> [options]

# Windows (use py if python is not on PATH)
py -m backend.cli <company_name> [options]
```

| Flag | Type | Description |
|------|------|-------------|
| `--location <text>` | string | Location filter (e.g., "Remote"). |
| `--url <text>` | string | Custom career‑page URL (bypasses auto‑construction). |
| `--salary-min <float>` | float | Minimum salary filter. |
| `--salary-max <float>` | float | Maximum salary filter. |
| `--max-concurrency <int>` | int | Override per‑domain concurrency. |
| `--jitter-max <float>` | float | Override max retry jitter. |

Example:
```bash
python -m backend.cli Google --max-concurrency 10 --jitter-max 1.0
```

---

## Testing

```bash
# macOS / Linux / WSL2
pytest -v

# Windows
py -m pytest -v
```

Tests use `respx` for HTTP mocking — no real network calls.

See [UPGRADE_ROADMAP.md](UPGRADE_ROADMAP.md) for recent improvements and testing status.

---

## Monitoring

`GET /agents/health` (requires `X-API-Key` if `API_KEY` is set) returns per-agent stats for
every LLM provider and job-source adapter:

```json
{
  "llm:gemini": {"status": "healthy", "calls": 42, "failures": 1, "failure_rate": 0.024, "avg_latency_ms": 812.3, "last_error": null},
  "source:JSearchAdapter": {"status": "degraded", "calls": 20, "failures": 5, "failure_rate": 0.25, "avg_latency_ms": 1340.0, "last_error": "..."},
  "source:_CompanyScraperAdapter": {"status": "unknown", "calls": 0, "failures": 0, "failure_rate": 0.0, "avg_latency_ms": 0.0}
}
```

`status` is `unknown` (no calls yet), `healthy`, `degraded` (>20% failure rate), or `down`
(every call so far failed). This is per-process, in-memory — resets on restart, and isn't
shared across workers/instances (same caveat as the in-memory rate limiter, see Troubleshooting).
For multi-worker production, point this at a shared store (Redis) instead.

---

## Troubleshooting

**Every request gets `401 Unauthorized`**
Two separate causes, both now fixed at the source:
1. The frontend previously never sent `X-API-Key` at all — fixed, it now sends it from `VITE_API_KEY` in `frontend/.env`, on every call.
2. `.env.example` used to ship `API_KEY=your_secure_api_key_here` — a real, non-empty value, not an obvious placeholder — so anyone who ran `cp .env.example .env` without editing that line got auth silently turned on with a key the frontend had no way to know about. Fixed: the shipped default is now `API_KEY=` (empty = open access, the intended dev default).

**If you still see this after updating:** you likely have an old `.env` file (from before this fix) still carrying the literal value `your_secure_api_key_here`. Open `backend/.env` (or wherever your `.env` lives) and either clear that line to `API_KEY=`, or set `VITE_API_KEY` in `frontend/.env` to that exact same value. Restart both the backend and the frontend dev server after editing — env files are only read at process start.

**`pymysql.err.DataError: (1406, "Data too long for column 'source_id'")`**
`Base.metadata.create_all()` only creates tables that don't exist yet — it never widens a column
on a table that's already there. If your `jobs` table was created before `source_id` grew to
`VARCHAR(512)` in `models.py`, the live column stays at its old, shorter width forever and any
long source ID (JSearch's RapidAPI job IDs can run past 500 chars) blows the insert.
Fixed as of this version: `backend/core/db.py:sync_schema()` runs on every startup, compares each
`String` column against the model, and `ALTER TABLE ... MODIFY` widens any that drifted — no
manual migration needed, just restart the backend. `normalize.py:safe_source_id()` also hashes
any source ID over 512 chars to a `sha256:...` digest as a second line of defense.

**One bad job in a search response used to 500 the whole `/jobs/search` call**
A single row that failed to insert (bad data, DB error) aborted the whole request and the other
55 fetched jobs were lost too. Each job insert is now wrapped in its own try/except with a
`db.rollback()` on failure — a bad row is logged and skipped, everything else still saves.

**Employment type / experience filters in the UI silently return nothing**
The frontend used to send display labels straight to the API (`'Full-time'`, `'Senior'`, raw
`.toLowerCase()`), while the backend stores normalized snake_case values (`'full_time'`,
`'senior'`). Hyphen vs. underscore and casing meant those filters never matched. Fixed — the
`EMP_TYPES`/`EXP_LEVELS`/`JOB_TYPE` option lists now carry the actual backend value, and the API
layer passes it through unmodified instead of re‑deriving it from the label.

---

## Contributing

1. Follow existing code style and type‑hint conventions.
2. Include unit tests for new functionality.
3. Update `UPGRADE_ROADMAP.md` for user‑facing changes.
4. Never hard‑code secrets — use `.env` (git‑ignored).

---

## License

MIT — see [LICENSE](LICENSE).
