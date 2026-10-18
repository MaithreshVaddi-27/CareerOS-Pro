# CareerOS-Pro — Deep Audit Report

Date: 2026-10-18

## Summary

Deep audit of backend (FastAPI/SQLAlchemy/MySQL), frontend (React/Vite), tests, docs, and repo hygiene. Critical runtime defects fixed, unwanted artifacts removed, folder structure normalized.

## Critical Issues Fixed

- MySQL SQL syntax error: `ORDER BY ... NULLS LAST` is not supported by MySQL; replaced with `Job.posted_at.is_(None), Job.posted_at.desc()` in `backend/main.py` and `backend/graph.py`. This unblocked `/jobs/search` and `/jobs/match`.
- Frontend lint warnings: unused `salary_max` and `top_k` parameters in `searchJobs` renamed to underscore-prefixed.

## Upgrades

- Removed Alembic folder, `alembic.ini`, and `tests/test_migrations.py`; schema now created from SQLAlchemy models.
- Updated Dockerfile, CI workflow, README, and docs to drop Alembic references.
- Deep-cleaned caches, `__pycache__`, `.DS_Store`, `qdrant_local`, `.remember`, `frontend/dist`.
- Updated `.gitignore` already excludes venv/node_modules/dist/caches; verified no tracked junk.
- Professional folder structure retained: backend/, frontend/, tests/, docs/.

## Degraded / Known Limitations

- MySQL must be running locally; no SQLite fallback (by design).
- Checkpointer reports `unknown` until first match request (lazy init) — health shows degraded initially.
- Frontend has no automated test suite; `npm run build` is the only signal.
- API_KEY empty by default (open mode) — set for production.
- Redis optional; in-process rate limiting under multi-worker.

## Verification

- Backend: `ruff check backend/ tests/` passed; `pytest` 245 passed, 55 skipped.
- Frontend: `npm run lint` clean; `npm run build` succeeded.
- Black-box: backend started, `/health` and `/jobs/search` and `/jobs/match` returned 200 with data.
