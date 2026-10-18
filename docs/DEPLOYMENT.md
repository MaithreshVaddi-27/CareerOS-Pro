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
python -c "from backend.core.db import Base, engine; Base.metadata.create_all(engine)"          # create the schema
python run.py
```

`run.py` loads `.env` from the project root, fixes `sys.path`, and starts uvicorn on
`127.0.0.1:8000`.

> **The app does not create tables.** Schema is owned by Alembic. Run
> `python -c "from backend.core.db import Base, engine; Base.metadata.create_all(engine)"` after cloning, and again after pulling any change that adds a
> revision. Skipping it means the first query fails on a missing `jobs` table.

Optional flags:

```bash
python run.py --host 0.0.0.0 --port 8080 --reload --workers 4
```

> `--workers > 1` makes rate limiting per-process — each worker keeps its own
> counters, so the effective ceiling is `RATE_LIMIT_PER_MINUTE x workers`. Set
> `REDIS_URL` to share one counter across workers.

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

Or point `MYSQL_HOST` at an existing instance. Then create the schema:

```bash
python -c "from backend.core.db import Base, engine; Base.metadata.create_all(engine)"
```

Before pulling a change that adds a migration, run it as part of your deploy:

```bash
python -c "from backend.core.db import Base, engine; Base.metadata.create_all(engine)" && python run.py
```

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
docker compose exec backend python -c "from backend.core.db import Base, engine; Base.metadata.create_all(engine)"
```

Redis for shared rate-limit counters is behind a profile, so it stays optional:

```bash
docker compose --profile ratelimit up --build
```

Set `REDIS_URL=redis://redis:6379/0` in `.env` when you enable it.

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

- [ ] `python -c "from backend.core.db import Base, engine; Base.metadata.create_all(engine)"` run against the deployed database
- [ ] `APP_ENV=production` — enables durable MySQL checkpoints
- [ ] `CHECKPOINT_STRICT=true` — fail fast rather than lose checkpoints silently
- [ ] `REDIS_URL` set **if** running more than one worker
- [ ] `API_KEY` is long and random
- [ ] `RATE_LIMIT_PER_MINUTE` set deliberately
- [ ] `ALLOWED_ORIGINS` lists only real frontend origins
- [ ] MySQL 8.x
- [ ] `.env` is gitignored and not baked into the image
- [ ] TLS terminated in front of the API
- [ ] `COMPANY_SCRAPER_USER_AGENT` identifies you with a contact URL
- [ ] Health checks pointed at `/health/ready`

> ### ⚠️ `API_KEY` is not a secret, and cannot be made one this way
>
> The checklist item above deliberately does *not* say "rotated out of band".
> That claim used to be here and it was wrong.
>
> `docker-compose.yml` passes `VITE_API_KEY` to the frontend as a **build arg**,
> and Vite inlines every `VITE_*` variable into the JavaScript bundle at build
> time. Verified: building with `VITE_API_KEY=SUPERSECRET_PROBE_KEY` produces a
> `dist/assets/*.js` containing that literal string.
>
> So the key the browser sends is readable by anyone via view-source, and the
> API is directly callable without going through the browser at all. `CORS`
> does not help here — it constrains browsers, not `curl`.
>
> What `API_KEY` actually buys you:
>
> - it stops anonymous drive-by traffic that does not know to look for it;
> - it keeps casual browser consoles and stray scripts out;
> - it rate-limits a known caller instead of the whole internet.
>
> What it does **not** buy you: any authentication or authorisation guarantee.
> Treat it as a shared quota token, not a credential. If you need real
> identity — per-user data, private resume uploads, anything you would be
> uncomfortable seeing in a public JS bundle — that needs session auth, which
> this stack does not have.
>
> If you would rather not ship the key at all, set `API_KEY=` empty in `.env`
> and the frontend stops sending it; the backend's `require_api_key` becomes a
> no-op and everything is open. That is a valid choice for a public read-only
> deployment, and it is the honest one.

### Process model

Run a **single** worker unless `REDIS_URL` is set. With in-process counters, N workers
enforce N× the intended limit.

```bash
python run.py --host 0.0.0.0 --port 8000 --workers 1
# or, with shared counters:
REDIS_URL=redis://redis:6379/0 python run.py --host 0.0.0.0 --port 8000 --workers 4
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

### Job retention

A posting that disappears upstream is marked `is_active = false`, not deleted, so the
`jobs` table only ever grows. Prune those rows on a schedule:

```bash
python -m backend.retention --days 90            # dry run — reports, deletes nothing
python -m backend.retention --days 90 --yes      # delete in bounded batches
```

| Flag | Default | Effect |
|---|---|---|
| `--days` | `90` | Inactive for at least this long |
| `--batch-size` | `1000` | Max rows deleted per run |
| `--yes` | off | Without it, nothing is deleted |

Each run deletes at most `--batch-size` rows, so the transaction stays short even on a
badly overgrown table — re-run until it reports nothing left. Eligibility is keyed on
`updated_at` (which the deactivation itself bumps), so a freshly-refreshed posting is
never expired early, and active rows are never eligible at any age.

Schedule it alongside your other jobs, e.g. daily at 03:17:

```cron
17 3 * * * cd /srv/careeros && backend/venv/bin/python -m backend.retention --days 90 --yes
```

Both commands must run from the repo root with the backend virtualenv active.

---

## Troubleshooting

**`ValidationError` on startup / `MYSQL_PASSWORD` missing**
`MYSQL_PASSWORD` and `API_KEY` have no defaults. Check `.env` exists in the project
root and that both are set. `run.py` reads `.env` before importing `config`, but
`python -m backend.cli` does not — export the variables or use `python run.py`.

**`Data too long for column 'source_id'`**
The table predates a widened model column. Run the revision that widens it:

```bash
python -c "from backend.core.db import Base, engine; Base.metadata.create_all(engine)"
```

If no such revision exists, the model and migrations have drifted — `schema review`
will say so. Generate one with
`schema migration (manual) -m "widen source_id to 512"`, review it, and commit.

**Checkpoints not surviving restarts**
Either `APP_ENV=development` (in-memory by design), or MySQL 9.x. Check `/health` —
`checks.checkpoint.kind` shows `memory` with a `DEGRADED` detail. Set
`CHECKPOINT_STRICT=true` so this fails at startup instead.

**`429` too aggressive in production**
Confirm proxy headers are forwarded — without `X-Forwarded-For` every client shares
one bucket. Confirm `REDIS_URL` if you run multiple workers. Then check
`RATE_LIMIT_PER_MINUTE`.

**Career page returns nothing**
Run the CLI against that company to see which URL it resolves to and what it parses.
Raise `COMPANY_SCRAPER_MAX_URL_PROBES`, or add the ATS path to
`COMPANY_CAREER_PAGE_PATTERNS`. A 403 is usually bot detection; the scraper obeys
`robots.txt` and will not try to work around a block.

**A company resolves to nothing and the log says "robots.txt disallows"**

That is the scraper obeying the site, not a bug. Check
`https://<company>/robots.txt` — a `Disallow: /careers` line will stop the probe
dead. Point `COMPANY_CAREER_PAGE_PATTERNS` at a path the site allows, or use
their ATS board (Greenhouse/Lever/Ashby) instead.

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
- [ARCHITECTURE.md](ARCHITECTURE.md) — how it fits together