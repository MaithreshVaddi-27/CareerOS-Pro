# Deployment

Local development, Docker, and production. See
[CONFIGURATION.md](CONFIGURATION.md) for what each variable does.

---

## Requirements

| Component | Version | Note |
|---|---|---|
| Python | 3.12 | Backend |
| Node.js | 20+ | Frontend build |
| MySQL | **8.0+** | See the warning below |

> **MySQL 9.x will break LangGraph checkpoints.** `AIOMySQLSaver.setup()` creates
> generated columns that default to `utf8mb4` with a checksum, and MySQL 9 disallows
> MD5 for that. The app catches this and falls back to in-memory checkpoints with a
> warning, so it stays up — but **checkpoints stop being durable across restarts.**
> Use MySQL 8.x in production.

---

## Local development

### 1. Configure

```bash
git clone https://github.com/MaithreshVaddi-27/CareerOS-Pro.git
cd CareerOS-Pro
cp .env.example .env
```

Set the two required values:

```dotenv
MYSQL_PASSWORD=your-password
API_KEY=choose-a-long-random-string
```

### 2. Backend

```bash
python -m venv backend/venv
source backend/venv/bin/activate
pip install -r requirements.txt
python run.py
```

`run.py` loads `.env` from the project root, fixes `sys.path`, and starts uvicorn on
`127.0.0.1:8000`.

Optional flags:

```bash
python run.py --host 0.0.0.0 --port 8080 --reload --workers 4
```

> `--workers > 1` makes rate limiting per-process. See
> [AUDIT.md](AUDIT.md#a2-rate-limiting-is-per-process).

### 3. MySQL

Docker is the lowest-friction option:

```bash
docker run -d --name careeros-mysql \
  -e MYSQL_ROOT_PASSWORD=rootpass \
  -e MYSQL_DATABASE=careeros \
  -e MYSQL_USER=careeros \
  -e MYSQL_PASSWORD=your-password \
  -p 3306:3306 \
  mysql:8
```

Or point `MYSQL_HOST` at an existing instance. On first start the app creates the
schema from the SQLAlchemy models and reconciles any `VARCHAR` columns that are
narrower than the model — see [AUDIT.md](AUDIT.md#a1-no-database-migrations) for what
this does *not* handle.

### 4. Frontend

```bash
cd frontend
npm install
cp .env.example .env      # set VITE_API_BASE
npm run dev               # → http://127.0.0.1:5173
```

---

## Docker Compose

Runs MySQL, the backend and the frontend together.

```bash
cp .env.example .env      # edit MYSQL_PASSWORD + API_KEY
docker compose up --build
```

Useful variations:

```bash
docker compose up --build -d          # detached
docker compose logs -f backend        # tail backend
docker compose exec backend pytest    # run tests in the container
docker compose down -v                # stop, and drop the volume
```

The compose file builds from the repo-root `Dockerfile` for the backend and
`frontend/Dockerfile` for the frontend.

---

## Production

### Checklist

- [ ] `APP_ENV=production` — enables durable MySQL checkpoints
- [ ] `API_KEY` is long, random, and rotated out of band
- [ ] `RATE_LIMIT_PER_MINUTE` set deliberately
- [ ] `ALLOWED_ORIGINS` lists only real frontend origins
- [ ] MySQL 8.x
- [ ] `.env` is gitignored and not baked into the image
- [ ] TLS terminated in front of the API
- [ ] `COMPANY_SCRAPER_USER_AGENT` identifies you with a contact URL
- [ ] Health checks pointed at `/health/ready`

### Process model

Run a **single** worker unless you have moved rate limiting to shared storage. The
in-process limiter means N workers enforce N× the intended limit.

```bash
python run.py --host 0.0.0.0 --port 8000 --workers 1
```

### Behind a reverse proxy

Two things need configuring explicitly.

**Trust the proxy.** Rate limiting keys on client IP. Without proxy headers every
request appears to come from the proxy, so all clients share one bucket:

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

**Forward the API key.** It arrives as a header, so the default `proxy_pass` is
sufficient — do not strip it.

### Health checks

| Endpoint | Use for |
|---|---|
| `/health/ready` | Liveness/readiness probes. `200` servable, `503` not. |
| `/health` | Dashboards. `200` with `"status": "degraded"` when a component is unhealthy. |
| `/agents/health` | Per-agent latency and failure rates. Requires the API key. |

---

## CLI

Scrape a single company to JSON — useful for testing scraper configuration without
starting the API.

```bash
python -m backend.cli "Acme Corp"
python -m backend.cli "Acme Corp" --location Berlin
python -m backend.cli "Acme Corp" --url https://boards.greenhouse.io/acme
python -m backend.cli "Acme Corp" --salary-min 80000 --salary-max 150000
python -m backend.cli "Acme Corp" --max-concurrency 2 --jitter-max 0.2
```

| Flag | Default | Effect |
|---|---|---|
| `--location` | — | Case-insensitive location filter |
| `--url` | — | Exact career page URL; skips URL discovery |
| `--salary-min` / `--salary-max` | — | Salary range filter |
| `--max-concurrency` | from config | Per-domain request cap |
| `--jitter-max` | from config | Retry jitter ceiling |

---

## Troubleshooting

**`ValidationError` on startup / `MYSQL_PASSWORD` missing**
`MYSQL_PASSWORD` and `API_KEY` have no defaults. Check `.env` exists in the project
root and that both are set. `run.py` reads `.env` before importing `config`, but
`python -m backend.cli` does not — export the variables or use `python run.py`.

**`Data too long for column 'source_id'`**
The table predates a widened model column. `sync_schema()` widens `VARCHAR` at startup,
so this means the app started without reaching that code — usually a `create_all`
failure earlier in `lifespan`. Check the startup logs. Or widen manually:
`ALTER TABLE jobs MODIFY COLUMN source_id VARCHAR(512);`

**Checkpoints not surviving restarts**
Either `APP_ENV=development` (in-memory by design) or MySQL 9.x. Confirm the
`AIOMySQLSaver` fallback warning in the logs.

**`429` too aggressive in production**
Confirm proxy headers are forwarded — without `X-Forwarded-For` every client shares
one bucket. Then confirm `RATE_LIMIT_PER_MINUTE`.

**Career page returns nothing**
Run the CLI against that company to see which URL it resolves to and what it parses.
Raise `COMPANY_SCRAPER_MAX_URL_PROBES`, or add the ATS path to
`COMPANY_CAREER_PAGE_PATTERNS`. A 403 is usually bot detection — see
[AUDIT.md](AUDIT.md#a6-career-page-scraping-has-no-robots-tolerance).

**Frontend builds but shows no data**
Check `VITE_API_BASE` points at the backend, and that CORS allows the frontend origin
via `ALLOWED_ORIGINS`.

**Empty results from every source**
Confirm `JOB_API_MODE`. In `mock` mode there is no network I/O and results are fixed
fixtures. In `live` mode with no credentials configured, only the free sources run.

---

## Related

- [CONFIGURATION.md](CONFIGURATION.md) — every variable
- [API.md](API.md) — endpoint reference
- [AUDIT.md](AUDIT.md) — known limitations before you deploy
- [ARCHITECTURE.md](ARCHITECTURE.md) — how it fits together