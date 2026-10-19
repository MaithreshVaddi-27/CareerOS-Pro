# Changelog

## Phase 3 — Security and match pipeline hardening (2026-10-19)

Status: shipped and pushed (`99be611`).

### Fixed
- Redacted secrets from `GET /agents/health`; Gemini provider errors no longer leak the API key.
- `POST /jobs/match` no longer 500s: `sources` is part of `MatchRequest` and is threaded into the graph.
- `model_name` is honored and validated; invalid ids fall back instead of failing inside a graph node.
- `max_salary` filtering added to both Python and SQL passes.
- Shared `now` timestamp keeps the SQL prefilter from being stricter than the Python pass.
- Qdrant collection singleton is only published after successful initialization.
- Embedding vectorizer is cached with `lru_cache`.
- Frontend no longer overrides the server's verification verdict and now sends `salary_max`.

### Added
- `tests/test_pass3_hardening.py` (37 guards).
- `tests/test_match_pipeline.py` (11 integration/contract tests).
- Backend suite: 315 passed, 1 skipped.

### Upgrades
- `docs/API.md`: `max_salary`, `posted_within_days`, `sources`, and `model_name` bounds.
- `README.md`: salary ceiling and source-name aliasing documented.

### Degradations
- None introduced.
- Local MySQL remains unavailable in this environment; match tests use the SQLite session fixture pattern.

## Phase 2 — Deep audit and native deployment (2026-10-18)

Status: shipped.

### Fixed
- Docker removed; deployment is native via systemd and nginx.
- `create_all()` creates missing tables and indexes.
- SSRF protection added for verification and scraping.
- Rate limiting, checkpointing, pagination, retention, and N+1 query fixes.
- React correctness fixes found by the oxlint gate.

### Verification
- `ruff check backend/ tests/`: clean
- `pytest`: 263 passed, 1 skipped
- `oxlint --deny correctness src/`: clean
- `npm run build`: 433 modules, 108 kB gzip

## Phase 1 — Production hardening and multi-source engine

Status: shipped.

### Added
- Multi-source job aggregation, normalization, deduplication, and filtering.
- Hybrid keyword + semantic ranking with grounded LLM explanations.
- Company career-page scraper.
- Native deployment assets and configuration documentation.
