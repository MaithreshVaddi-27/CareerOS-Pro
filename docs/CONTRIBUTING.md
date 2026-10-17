# Contributing

## Setup

```bash
git clone https://github.com/MaithreshVaddi-27/CareerOS-Pro.git
cd CareerOS-Pro
python -m venv backend/venv && source backend/venv/bin/activate
pip install -r requirements.txt
pip install "ruff==0.17.1"          # pinned — see "Why the pin" below
```

Set `MYSQL_PASSWORD` and `API_KEY` in `.env` (see [CONFIGURATION.md](CONFIGURATION.md)).

## The loop

```bash
ruff check backend/ tests/ alembic/    # lint
ruff check backend/ tests/ alembic/ --fix   # autofix (imports, safe rewrites)
python -m pytest -v                    # tests — includes `alembic check`
cd frontend && npm run build           # frontend has no test suite
```

Schema changes are the one thing you cannot skip:

```bash
alembic revision --autogenerate -m "describe the change"   # then edit + review
alembic check          # exit 0 = models and migrations agree (also in CI)
```

### Why the pin

Ruff's default rule set is **not** stable across releases — 0.17 expanded it from
roughly five rules to four hundred. CI therefore pins `ruff==0.17.1` and the rule set
is pinned in `pyproject.toml`. Without both, `ruff check` means something different
every time the runner resolves a new ruff, and CI fails for reasons unrelated to your
change. If you bump ruff, bump the pin in `.github/workflows/ci.yml` in the same
commit.

## Where code goes

**`main.py` and `graph.py` hold no business logic.** `graph.py` is orchestration —
each node delegates to a service. If you are writing an `if` about job data in either
file, it belongs in `services/`.

Dependency direction is strict:

```
main.py ─┐
graph.py ─┼─→ services/* ─┐
cli.py  ───┘              ├─→ core/config.py  (leaf)
                          └─→ schemas/job.py  (leaf)
```

- `core/*` never imports `services/*`.
- `services/*` never imports `main`, `graph` or `cli`.
- Nothing imports from `tests/`.

## Conventions

### Fields ending in `_raw` hold source text

`RawJobPosting.location_raw`, `employment_type_raw`, `posted_at_raw` and
`salary_raw` are **unmapped**. Adapters emit what the source said; `normalize.py`
does the mapping.

Getting this wrong fails quietly. Mapping a value in the adapter means
`parse_employment_type("full_time")` looks up a raw key, misses, and returns
`unknown` — the job still appears, just with no employment type, so it will not match
an employment filter.

The company scraper is the newest adapter and follows this rule.

### Unknown stays unknown

Do not fill a field you could not read. Not with a default, not from the job title,
not from a plausible guess. `unknown` is a valid, load-bearing value that
`filters.py` reads.

### Add a field to both layers

A new job attribute needs the model column, the schema field, the parse in
`normalize.py`, **and** a migration:

```bash
alembic revision --autogenerate -m "add seniority_band to jobs"
```

Review the generated file before committing — autogenerate catches structure, not
intent. Never edit a revision that has been applied anywhere; add a new one instead.

## Testing

Tests live in `tests/` and are plain `pytest` — no database required, no fixtures
beyond `conftest.py`.

| File | Covers |
|---|---|
| `test_normalize.py` | URL/content hashing, location, employment, experience, salary, dates |
| `test_company_scraper.py` | Card extraction, field extraction, URL discovery, cache, retry |
| `test_dedup.py` | `upsert_job` deduplication |
| `test_filters.py` | Each hard filter in isolation and combined |
| `test_job_adapter.py` | Adapter selection, mock/live mode, fan-out |
| `test_match_prompt.py` | Prompt invariants and explain-node concurrency |
| `test_middleware.py` | Rate limiting (both backends), API-key auth, response headers |
| `test_migrations.py` | Migrations match the models; `.env.example` matches `Settings` |
| `test_checkpoint.py` | Checkpointer selection, degradation, `CHECKPOINT_STRICT` |
| `test_verification.py` | Liveness checking |

### Conventions

- Name the behaviour, not the function: `test_location_filter_is_applied`, not
  `test_location_2`.
- **A regression test must fail without its fix.** If you fix a bug, revert the fix,
  confirm the test goes red, then restore it. A test that passes both ways proves
  nothing. Every regression test in `test_company_scraper.py` was verified this way.
- Keep the smallest thing that fails for the right reason. Prefer a real assertion on
  observable behaviour over mocking internals.
- Async tests use `asyncio_mode = auto` (set in `pytest.ini`) — no `@pytest.mark.asyncio`
  decorator needed.

### Changing configuration in a test

Settings are `@lru_cache`d and read at module scope. To change them mid-test:

```python
from backend.core.config import get_settings
get_settings.cache_clear()
```

`test_job_adapter.py` uses this. Call it again afterwards so tests stay independent.

## Commit messages

Conventional Commits. Describe **why**, not just what.

```
fix(scraper): emit raw employment tokens so normalize can map them

Pre-mapping in the adapter caused double normalization -
parse_employment_type("full_time") missed the raw key and
returned "unknown", so every scraped job lost its employment type.
```

```
refactor(tests): move hash tests to test_normalize, keep the stronger versions

The copies in test_dedup were weaker subsets of the same assertions.
```

## PR checklist

- [ ] `ruff check backend/ tests/` passes
- [ ] `python -m pytest` passes
- [ ] `npm run build` passes if you touched the frontend
- [ ] New behaviour has a test
- [ ] Bug fixes have a test verified to fail without the fix
- [ ] No secrets, `.env` values or API keys in the diff
- [ ] New config vars documented in `.env.example` **and** [CONFIGURATION.md](CONFIGURATION.md)
- [ ] Commit message explains why

## Adding things

| To add | Do this |
|---|---|
| A job source | Subclass `JobSourceAdapter`, register in `get_adapter()` behind a credential check |
| An ATS layout | Add the path to `COMPANY_CAREER_PAGE_PATTERNS` — no code change |
| A hard filter | Add to `filters.py` and to `SearchRequest` |
| An LLM provider | Implement `LLMProvider` in `model_router.py`, add to `_get_provider()` and the chain |
| A table | Add to `models/`, re-export from `models/__init__.py`, then `alembic revision --autogenerate -m "add X"` |
| A response field | Add to `schemas/job.py` — the OpenAPI schema follows automatically |

## Before you ship something significant

Read [AUDIT.md](AUDIT.md). Several limitations listed there will silently affect new
features — notably the absence of database migrations and the per-process rate limiter.