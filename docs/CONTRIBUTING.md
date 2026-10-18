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
ruff check backend/ tests/            # backend lint
ruff check backend/ tests/ --fix      # autofix (imports, safe rewrites)
python -m pytest -v                   # backend suite
cd frontend
npx oxlint --deny correctness src/    # frontend lint
npm run build                         # frontend has no test suite
```

Schema changes are the one thing you cannot skip:

```bash
python -c "from backend.core.db import create_all; create_all()"   # apply new tables
```

Use `create_all()`, never `Base.metadata.create_all(engine)` — the latter exits `0`
and creates nothing, because `Base` has no models registered until they are imported.
`create_all()` also creates missing *indexes*, so `index=True` takes effect on
re-run. It does not `ALTER` columns — widening one needs manual SQL (see
[DEPLOYMENT.md](DEPLOYMENT.md#troubleshooting)).

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

`services/` splits in two, and the split is load-bearing:

- **`sourcing/`** — talks to the outside world about jobs: source adapters, the
  career-page scraper, parsing, dedup, and the liveness check.
- **`matching/`** — turns rows we already hold into a ranked, explained answer:
  filters, reranking, embeddings, vector store, LLM routing.

Nothing in `matching/` may fetch a job, and the two never import each other. Both
rules are enforced by `tests/test_boundaries.py` — if you break one, the suite tells
you rather than a reviewer six weeks from now.

Dependency direction is strict:

```
main.py ─┐
graph.py ─┼─→ services/* ─┐
cli.py  ───┘              ├─→ core/config.py  (leaf)
retention.py               └─→ schemas/job.py  (leaf)
```

- `core/*` never imports `services/*`.
- `services/*` never imports `main`, `graph`, `cli` or `retention`.
- `services/matching/*` and `services/sourcing/*` never import each other.
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
python -c "from backend.core.db import create_all; create_all()"
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
| `test_filters_sql.py` | SQL prefilter is a superset of the Python predicate |
| `test_job_adapter.py` | Adapter selection, mock/live mode, fan-out |
| `test_match_prompt.py` | Prompt invariants and explain-node concurrency |
| `test_middleware.py` | Rate limiting (both backends), API-key auth, response headers |
| `test_hardening.py` | Production invariants: SSRF guard, schema command, auth, redaction |
| `test_checkpoint.py` | Checkpointer selection, degradation, `CHECKPOINT_STRICT` |
| `test_verification.py` | Liveness checking |
| `test_retention.py` | Dry-run default, bounded batches, never touches active rows |
| `test_pagination.py` | `limit`/`offset`/`top_k` bounds |
| `test_routes.py` | Route handlers, error shapes, `/health` + `/health/ready` assembly |
| `test_boundaries.py` | Import layering: core/services split, sourcing/matching independence |

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
- [ ] `npx oxlint --deny correctness src/` passes if you touched the frontend
- [ ] `npm run build` passes if you touched the frontend
- [ ] New behaviour has a test
- [ ] Bug fixes have a test verified to fail without the fix
- [ ] No secrets, `.env` values or API keys in the diff
- [ ] New config vars documented in `.env.example` **and** [CONFIGURATION.md](CONFIGURATION.md)
- [ ] New dependencies pinned exactly (`==`) and the suite still green
- [ ] Commit message explains why

## Adding things

| To add | Do this |
|---|---|
| A job source | Subclass `JobSourceAdapter`, register in `get_adapter()` behind a credential check |
| An ATS layout | Add the path to `COMPANY_CAREER_PAGE_PATTERNS` — no code change |
| A hard filter | Add to `filters.py` and to `SearchRequest` |
| An LLM provider | Implement `LLMProvider` in `model_router.py`, add to `_get_provider()` and the chain |
| A table | Add to `models/`, re-export from `models/__init__.py`, then run `create_all()` |
| A response field | Add to `schemas/job.py` — the OpenAPI schema follows automatically |

## Before you ship something significant

Two limitations will silently affect new features: the rate limiter is per-process
unless `REDIS_URL` is set, and the LangGraph checkpoint saver downgrades to in-memory on
MySQL 9.x unless `CHECKPOINT_STRICT=true`. Both are documented in
[DEPLOYMENT.md](DEPLOYMENT.md).