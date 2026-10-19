# CareerOS-Pro — Phase, Bug & Status Register

## Phase 1 — Production hardening & multi-source engine
Status: shipped. Multi-source ingestion, hybrid ranking, company scraper, native deploy.

## Phase 2 — Deep security & performance audit
Status: shipped. No critical issues. ruff/pytest/oxlint/build green.

## Phase 3 — Code quality & optimization
Status: shipped. No dead code, bundle stable (~108 kB gzip).

## Phase 4 — Source-name consistency
Status: shipped. `company_scraper` alias normalized in `get_adapter()`.

## Verification baseline
- ruff: clean
- pytest: 315 passed, 1 skipped
- oxlint: clean
- npm build: 433 modules, ~108 kB gzip

## Known limitations
- Local MySQL unavailable; match tests use SQLite fixture.
- Rate limiting per-process unless REDIS_URL set.
- Checkpointing degrades to in-memory on MySQL 9.x unless CHECKPOINT_STRICT=true.
- create_all() creates tables/indexes, not column alters.
