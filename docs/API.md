# API Reference

Base URL: `http://127.0.0.1:8000` (local) or your deployed host.
Interactive docs: `/docs` (Swagger) and `/redoc`.

---

## Authentication

Every route except `/health`, `/health/ready` and the framework docs requires:

```http
X-API-Key: <API_KEY>
```

An empty `API_KEY` leaves the gate **open**, which is the local-development default. Production startup rejects an empty key. If the bundled browser frontend sends `VITE_API_KEY`, remember it is visible in the frontend assets and provides no user identity or authorization.

A missing or wrong key returns **401** with a message naming the header. The bundled
frontend needs `VITE_API_KEY` set to the same value or it will 401 on every call.

---

## Rate limiting

Fixed-window, per client IP, applied by `RateLimitMiddleware`. `/health` is exempt
(`/health/ready` is **not**).

- Sustained limit: `RATE_LIMIT_PER_MINUTE` (default `60`)
- Burst allowance: `2 ×` the per-minute limit within a 10-second window
- Response: `429` with `Retry-After: 60`

Counters live in Redis when `REDIS_URL` is set, otherwise in-process — and in-process
counters are **per worker**, so `--workers > 1` multiplies the effective limit by the
worker count unless `REDIS_URL` is set. See
[CONFIGURATION.md](CONFIGURATION.md#security-and-networking). `/health` reports which
backend is active.

---

# Endpoints

## `GET /health`

Unauthenticated. Component-by-component health.

```jsonc
// 200
{
  "status": "healthy",          // healthy | degraded
  "checks": {
    "database":        { "status": "ok", "detail": "MySQL connection successful" },
    "rate_limit":      { "status": "ok", "backend": "redis",
                         "detail": "rate_limit_per_minute=60, backend=redis (shared)" },
    "checkpoint":      { "status": "ok", "kind": "mysql",
                         "detail": "AIOMySQLSaver (restart-durable)" },
    "embedding_model": { "status": "ok", "detail": "HashingVectorizer embedding ready" },
    "vector_store":    { "status": "ok", "detail": "Qdrant client initialized" }
  }
}
```

`status: "degraded"` means at least one component is unhealthy but the process is
still serving. Two components report degraded for reasons that matter:

- **`rate_limit`** — degraded when `APP_ENV=production` and `REDIS_URL` is unset.
  Per-process counters under-limit by the worker count.
- **`checkpoint`** — degraded when the checkpointer silently fell back to in-memory,
  i.e. checkpoints will not survive a restart. Set `CHECKPOINT_STRICT=true` in
  production to refuse that fallback at startup instead.

`checkpoint.kind` is one of `mysql`, `memory`, `unavailable`, or `unknown` before the
first `/jobs/match` call resolves it.

## `GET /health/ready`

Unauthenticated readiness probe. `200` when the database answers, `503` otherwise.
Use this for container orchestration; use `/health` for dashboards.

## `GET /agents/health`

Authenticated. Per-agent call statistics from `core/monitoring.py`.

```jsonc
{
  "agents": {
    "llm:gemini":      { "calls": 12, "successes": 12, "failures": 0,
                         "avg_latency_ms": 184.2, "failure_rate": 0.0, "status": "healthy" },
    "source:jsearch":  { "calls": 40, "successes": 38, "failures": 2,
                         "avg_latency_ms": 812.7, "failure_rate": 0.05, "status": "healthy" }
  }
}
```

`status` is derived: `down` (no successes, ≥1 failure) → `degraded` (failure rate
> 20%) → `healthy` (≥1 success) → `unknown`.

## `POST /jobs/search`

Authenticated. Fan out to every configured source, normalize, dedup, hard-filter.

**Request** — all fields optional; an empty body returns everything.

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | string | `""` | Free text. Matched against title + description. |
| `location` | string | `null` | Alias-aware partial match; remote jobs match any location. |
| `employment_type` | string | `null` | `full_time` \| `part_time` \| `internship` \| `contract` \| `temporary` |
| `experience_level` | string | `null` | `intern` \| `fresher` \| `entry` \| `mid` \| `senior` |
| `remote_only` | bool | `false` | |
| `min_salary` | float | `null` | Rows with an unknown salary are **not** excluded. |
| `max_salary` | float | `null` | Ceiling on `salary_max`. Unknown salary is **not** excluded. |
| `posted_within_days` | int | `null` | `1`–`3650`. A job with unknown `posted_at` is not excluded. |
| `sources` | string[] | `null` | Overrides `JOB_SOURCES` for this request. At most 10 entries; each 1–64 chars. Accepts either the enabling name or the stored tag (see below). |
| `limit` | int | `50` | Page size, `1`–`200`. |
| `offset` | int | `0` | Rows to skip. |

**`sources` accepts both names.** One source has two spellings: the name that
enables it and the name rows are tagged with.

| Enabling name (`JOB_SOURCES`) | Stored `Job.source` |
|---|---|
| `company` | `company_scraper` |

Every other source uses the same string in both places. Either spelling now
matches rows from the career-page scraper, so `sources: ["company"]` and
`sources: ["company_scraper"]` are equivalent on this endpoint.

**Response** — `JobOut[]`, ordered by `posted_at` descending (nulls last), then
`created_at` descending. At most `limit` rows; no total count is returned, so an
empty array means either no match or that you paged past the end.

```jsonc
{
  "id": "0f3c…",
  "title": "Senior Backend Engineer",
  "company": "Acme Corp",
  "location_normalized": "Berlin, Germany",
  "employment_type": "full_time",
  "experience_level": "senior",
  "remote": "unknown",          // remote | onsite | hybrid | unknown
  "skills_required": [],
  "salary_min": 90000.0,
  "salary_max": 120000.0,
  "salary_currency": "EUR",
  "apply_url": "https://…",
  "posted_at": "2026-08-25T09:00:00Z",
  "source": "company_scraper",
  "is_active": true,
  "verified": false,
  "verified_at": null
}
```

> `location_normalized` preserves the source's casing (`"New York, NY"`, not
> `"New York, Ny"`) — filtering lowercases both sides before comparing.

## `GET /jobs/{job_id}`

Authenticated. One posting. `404` if unknown.

## `POST /jobs/match`

Authenticated. The full pipeline: search → hard filter → hybrid rank → grounded
explanation.

**Request**

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | string | **required** | |
| `candidate_skills` | string[] | `[]` | The model may only match from this list. |
| `top_k` | int | `10` | Page size, `1`–`100`. Was unbounded before Phase 2. |
| `offset` | int | `0` | Candidates to skip before ranking. |
| `location`, `employment_type`, `experience_level`, `remote_only`, `min_salary`, `max_salary`, `posted_within_days`, `sources` | | `null`/`false` | Same semantics as `/jobs/search`. |
| `llm_provider` | string | `null` | `auto` \| `gemini` \| `none` |
| `model_name` | string | `null` | Overrides `GEMINI_MODEL` for this request. Must be a bare model id (`[A-Za-z0-9._-]`, ≤64 chars); anything else is rejected with a 422. |

`sources` narrows **both** the adapter fan-out and the stored-row filter on this
endpoint, matching `/jobs/search`.

**Response** — `MatchedJobOut[]`, sorted by `hybrid_score` descending.

```jsonc
{
  "job": { /* JobOut */ },
  "keyword_score": 0.71,
  "semantic_score": 0.84,
  "hybrid_score":  0.79,
  "evidence": "python|fastapi|kubernetes",
  "matched_skills": ["Python", "FastAPI"],
  "missing_skills": ["Kubernetes"],
  "explanation": "Strong overlap on backend Python; no container experience stated.",
  "confidence": 0.8
}
```

`evidence` is the model's own quotation from the posting — check it against
`job.description` to audit the match. `confidence` is the model's self-report; the
router treats `< 0.6` as "ask the next provider".

## Behaviour without any LLM configured

The pipeline still runs. Every job comes back with scores populated and:

```jsonc
{ "matched_skills": [], "missing_skills": [],
  "explanation": "unknown — no LLM provider available or all providers failed",
  "confidence": 0.0 }
```

Ranking is deterministic and does not depend on the LLM.

## `POST /jobs/{job_id}/verify`

Authenticated. Confirms a posting is still live; sets `verified` / `verified_at` **only
on a positive confirmed result**.

Requires a reachable `apply_url`. If `FIRECRAWL_API_KEY` is unset the posting is
still reachability-checked but content is left unconfirmed, returning
`verified: false` with that reason — **not** an error. Reachability alone is not
sufficient to set `verified`, so the field stays `false`.

Without a Firecrawl key, use `GET /jobs/{job_id}` and treat `verified: false` as
"unverified", never as "dead".

---

## Errors

FastAPI's default shape:

```jsonc
{ "detail": "missing X-API-Key header — backend API_KEY is set, but this request sent none. …" }
{ "detail": "invalid X-API-Key — value doesn't match backend API_KEY" }
{ "detail": "job not found" }                                       // 404
{ "detail": "rate limit exceeded, try again shortly" }             // 429 + Retry-After
```

`422` appears only for malformed request bodies (FastAPI field validation).

**A failing source is not an error.** If every job source fails, `/jobs/search` returns
`200` with `[]`. Failures are logged and counted in `/agents/health`.

---

## Pagination and limits

Both endpoints paginate:

| Endpoint | Page size | Offset field |
|---|---|---|
| `/jobs/search` | `limit`, `1`–`200`, default `50` | `offset` |
| `/jobs/match` | `top_k`, `1`–`100`, default `10` | `offset` |

Filtering runs in SQL against indexed columns; the response is a bare array with no
total count. Paging with a `limit` smaller than the filtered set is therefore the
intended way to walk results.

Filtering happens in two passes: a SQL prefilter that is guaranteed never to exclude a
row the filter would otherwise keep, then the authoritative Python predicate. The SQL
pass can over-match on short location codes (see below) but never under-match.

> **Known imprecision.** `location` is matched in SQL with `LIKE`, which cannot express
> the Python matcher's word-boundary rule. `location=in` therefore also matches
> "london, united kingdom" at the SQL stage and `location=ny` matches "berlin, germany".
> The Python pass rejects both, so results are correct — just with a few extra rows
> read. The affected needles are enumerated in `tests/test_filters_sql.py`.

`limit`/`offset` were added for this; see [ARCHITECTURE.md](ARCHITECTURE.md).