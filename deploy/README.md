# deploy/

Native host deployment. No Docker — the stack runs directly on the box.

| File | What it is |
|---|---|
| `careeros-api.service` | systemd unit for the backend |
| `nginx-careeros.conf` | nginx site: serves the built SPA, proxies `/api/` to the backend |

## Install

Assumes the repo lives at `/srv/careeros`, owned by a `careeros` user.

```bash
sudo useradd --system --home /srv/careeros --shell /usr/sbin/nologin careeros
sudo git clone https://github.com/MaithreshVaddi-27/CareerOS-Pro.git /srv/careeros
sudo chown -R careeros:careeros /srv/careeros

sudo -u careeros python3.12 -m venv /srv/careeros/backend/venv
sudo -u careeros /srv/careeros/backend/venv/bin/pip install -r /srv/careeros/requirements.txt

sudo -u careeros cp /srv/careeros/.env.example /srv/careeros/.env
sudo -u careeros nano /srv/careeros/.env     # MYSQL_PASSWORD + API_KEY at minimum
```

Build the frontend. `VITE_*` values are inlined into the bundle at build time —
see the API key warning in [DEPLOYMENT.md](../docs/DEPLOYMENT.md#production).

```bash
cd /srv/careeros/frontend
npm ci
VITE_API_BASE=https://careeros.example.com npm run build
```

Schema, then start the services:

```bash
sudo -u careeros /srv/careeros/backend/venv/bin/python -c \
  "from backend.core.db import create_all; create_all()"

sudo cp deploy/careeros-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now careeros-api

sudo cp deploy/nginx-careeros.conf /etc/nginx/sites-available/careeros
sudo ln -s /etc/nginx/sites-available/careeros /etc/nginx/sites-enabled/careeros
sudo nginx -t && sudo systemctl reload nginx
```

## What runs where

| Process | Managed by | Notes |
|---|---|---|
| MySQL 8.x | `mysql.service` | Must be 8.x — 9.x breaks LangGraph checkpoints |
| backend | `careeros-api.service` | single worker unless `REDIS_URL` is set |
| frontend | nginx | static files only; Node is build-time |
| Qdrant | *none* | embedded local mode, `./qdrant_local`, inside the backend process |
| Redis | *optional* | only if `REDIS_URL` is set and you run >1 worker |

Embedded Qdrant is the reason no container is needed: with `QDRANT_URL` empty the
client opens `./qdrant_local` in-process. That directory is the one writable path
the systemd unit grants, and the file lock it takes is why the API must run as a
single process unless you point at a real Qdrant server.

## Day to day

```bash
sudo systemctl status careeros-api
sudo systemctl restart careeros-api
sudo journalctl -u careeros-api -f
curl -s localhost:8000/health/ready | jq .
```

Update:

```bash
sudo -u careeros git -C /srv/careeros pull
sudo -u careeros /srv/careeros/backend/venv/bin/pip install -r /srv/careeros/requirements.txt
sudo -u careeros /srv/careeros/backend/venv/bin/python -c \
  "from backend.core.db import create_all; create_all()"
sudo systemctl restart careeros-api
```

Retention is a cron entry — see [DEPLOYMENT.md](../docs/DEPLOYMENT.md#job-retention).

## macOS note

systemd units are Linux-only. On a Mac, run `python run.py` directly; the
hardening options in the unit (ProtectSystem, ReadWritePaths, RestrictNamespaces)
have no macOS equivalent and would need `launchd` plus the sandbox-exec tool.