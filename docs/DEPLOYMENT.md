# Deployment

Local development and production, both running natively on the host — no
container runtime. Ready-to-install unit files are in [`deploy/`](../deploy/README.md).
See [CONFIGURATION.md](CONFIGURATION.md) for what each variable does.

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
python -c "from backend.core.db import create_all; create_all()"          # create the schema
python run.py
```

`run.py` loads `.env` from the project root, fixes `sys.path`, and starts uvicorn on
`127.0.0.1:8000`.

> **The app does not create tables at startup** — no DDL runs in the lifespan, so
> environments cannot drift silently. Run
> `python -c "from backend.core.db import create_all; create_all()"` after cloning and
> again after any change that adds or widens a column. Skipping it means the first
> query fails on a missing table.
>
> It creates missing tables **and** missing indexes, so adding `index=True` to a
> model and re-running this does take effect on an existing database. It does not
> ALTER columns, drop tables, or change column types.
>
> Always use `create_all()`. The older
> `from backend.core.db import Base, engine; Base.metadata.create_all(engine)` form
> **exits 0 and creates nothing**: `Base` alone has an empty metadata registry,
> because model modules register themselves when imported. `create_all()` does
> that import first. `tests/test_hardening.py` pins this — the old form was a
> deploy that looked successful and had no schema.

Optional flags:

```bash
python run.py --host 0.0.0.0 --port 8080 --reload --workers 4
```

> `--workers > 1` makes rate limiting per-process — each worker keeps its own
> counters, so the effective ceiling is `RATE_LIMIT_PER_MINUTE x workers`. Set
> `REDIS_URL` to share one counter across workers.

### 3. MySQL

Install MySQL 8.x directly:

```bash
brew install mysql@8.4                       # macOS
sudo apt-get install -y mysql-server-8.0     # Debian/Ubuntu
```

Create the database and an application user — do not run the app as `root`:

```sql
CREATE DATABASE careeros CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'careeros'@'127.0.0.1' IDENTIFIED BY 'your-password';
GRANT ALL ON careeros.* TO 'careeros'@'127.0.0.1';
FLUSH PRIVILEGES;
```

An existing instance works too — just point `MYSQL_HOST` at it. Then create the schema:

```bash
python -c "from backend.core.db import create_all; create_all()"
```

Fold it into the restart so the app never serves against an old schema:

```bash
python -c "from backend.core.db import create_all; create_all()" && python run.py
```

### 4. Frontend

```bash
cd frontend
npm install
cp .env.example .env      # set VITE_API_BASE
npm run dev               # → http://127.0.0.1:5173
```

---

## Qdrant

There is nothing to install. With `QDRANT_URL` empty, `qdrant-client` opens an
embedded on-disk store at `./qdrant_local` inside the backend process.

Two consequences worth knowing:

- It takes a **directory lock**, so only one process may open it. That is the
  reason the API runs a single worker, and the reason `--workers > 1` needs a
  real Qdrant server (`QDRANT_URL`) or no vector search at all.
- The directory is the backend's only writable path. The systemd unit grants it
  via `ReadWritePaths`.

To use a real Qdrant server instead, point `QDRANT_URL` at it and the same code
path switches to HTTP — no code change.

## Redis

Optional. Only needed for shared rate-limit counters across multiple workers.

```bash
brew install redis-server      # macOS
sudo apt-get install -y redis-server   # Debian/Ubuntu
```

Set `REDIS_URL=redis://127.0.0.1:6379/0` in `.env` when you enable it.

---

## Production

### Checklist

- [ ] `python -c "from backend.core.db import create_all; create_all()"` run against the deployed database
- [ ] `APP_ENV=production` — enables durable MySQL checkpoints
- [ ] `CHECKPOINT_STRICT=true` — fail fast rather than lose checkpoints silently
- [ ] `REDIS_URL` set **if** running more than one worker
- [ ] `API_KEY` is long and random
- [ ] `RATE_LIMIT_PER_MINUTE` set deliberately
- [ ] `ALLOWED_ORIGINS` lists only real frontend origins
- [ ] MySQL 8.x
- [ ] `.env` is gitignored and readable only by the service user (`chmod 600`)
- [ ] TLS terminated in front of the API
- [ ] `COMPANY_SCRAPER_USER_AGENT` identifies you with a contact URL
- [ ] Health checks pointed at `/health/ready`

> ### ⚠️ `API_KEY` is not a secret, and cannot be made one this way
>
> The checklist item above deliberately does *not* say "rotated out of band".
> That claim used to be here and it was wrong.
>
> Vite inlines every `VITE_*` variable into the JavaScript bundle at build
> time, so `VITE_API_KEY` ships inside `dist/assets/*.js` for anyone who opens
> view-source. Verified: building with `VITE_API_KEY=SUPERSECRET_PROBE_KEY`
> produces a bundle containing that literal string.
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
enforce N× the intended limit — and embedded Qdrant takes a directory lock that only
one process can hold, so a second worker fails to start.

```bash
python run.py --host 127.0.0.1 --port 8000 --workers 1
# or, with shared counters AND a real Qdrant server:
REDIS_URL=redis://127.0.0.1:6379/0 python run.py --host 127.0.0.1 --port 8000 --workers 4
```

Bind to `127.0.0.1`, not `0.0.0.0` — nginx is the only thing that should reach the
API. `careeros-api.service` does this already.

### Running as a service

[`deploy/careeros-api.service`](../deploy/careeros-api.service) is a hardened unit:
non-root, `ProtectSystem=strict`, a seccomp-style syscall and address-family
allowlist, and `ReadWritePaths` granting write access to nothing but the Qdrant
store and the virtualenv. Full install steps are in
[`deploy/README.md`](../deploy/README.md).

It sets `EnvironmentFile=/srv/careeros/.env`, so the same `.env` drives local
development and production — change the path if you install elsewhere.

Because `run.py` exits non-zero when `APP_ENV=production` and `API_KEY` is empty, a
misconfigured production deploy fails loudly into a visible restart loop rather
than quietly serving open traffic.

### Behind a reverse proxy

[`deploy/nginx-careeros.conf`](../deploy/nginx-careeros.conf) is a complete
working config. The two things that are easy to get wrong:

**Trust the proxy.** Rate limiting keys on client IP. Without
`X-Forwarded-For` every request appears to come from nginx, so all clients share
one bucket and you get unexplained `429`s.

**Forward the API key.** It arrives as a header, so the default `proxy_pass` is
sufficient — do not strip it, and do not add a `proxy_set_header` for it.

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
The live table predates a widened model column. `create_all()` creates missing
tables and indexes but does **not** alter existing columns, so this needs a
manual `ALTER`:

```sql
ALTER TABLE jobs MODIFY source_id VARCHAR(512);
```

Check the current model length in `backend/models/job.py` and match it. There is
no migration tool in this project — schema changes are a model edit plus a
reviewed SQL statement applied to each environment.

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