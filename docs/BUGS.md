# Bug Register

## Phase 3 — Security and match pipeline hardening

| Bug | Status | Upgrade | Degradation |
|---|---|---|---|
| Gemini API key leaked through `/agents/health` | Fixed (`99be611`) | Sink-level `scrub_secrets()` in `AgentMonitor` | None |
| `POST /jobs/match` raised `AttributeError` on `req.sources` | Fixed (`99be611`) | `sources` added to `MatchRequest` and threaded into graph | None |
| Invalid `model_name` could 500 inside a graph node | Fixed (`99be611`) | Validated id with safe fallback | Invalid ids are ignored |
| `max_salary` request field was silently ignored | Fixed (`99be611`) | Python + SQL filters | Unknown salary remains eligible |
| SQL filter stricter than Python filter | Fixed (`99be611`) | Shared `now` timestamp | None |
| Qdrant singleton published before collection init | Fixed (`99be611`) | Publish after success | None |
| Embedding vectorizer was not cached | Fixed (`99be611`) | `lru_cache` | None |
| Frontend overrode server verification verdict | Fixed (`99be611`) | Use server result | None |
| Frontend never sent `salary_max` | Fixed (`99be611`) | Correct request field | None |

## Phase 4 — Source-name consistency

| Bug | Status | Upgrade | Degradation |
|---|---|---|---|
| `sources: ["company_scraper"]` excluded the company scraper from fan-out | Fixed (uncommitted) | Alias normalized in `get_adapter()` | None |

## Known limitations (deliberate)

- Local MySQL unavailable in this environment; match tests use the SQLite session fixture.
- Rate limiting is per-process unless `REDIS_URL` is set.
- Checkpointing degrades to in-memory on MySQL 9.x unless `CHECKPOINT_STRICT=true`.
