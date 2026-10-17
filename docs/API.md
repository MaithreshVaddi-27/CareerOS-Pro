# API Reference

Base URL: `http://127.0.0.1:8000` (local) or your deployed host.
Interactive docs: `/docs` (Swagger) and `/redoc`.

---

## Authentication

Every route except `/health`, `/health/ready` and the framework docs requires:

```http
X-API-Key: <API_KEY>
```

`API_KEY` has no default. With it unset the gate is **open** — intended for local
development only. The app logs a warning at startup when it sees an API key configured
alongside a production `APP_ENV` and a rate limit that is not clearly safe.

A missing or wrong key returns `403`.

---

## Rate limiting

Fixed-window, per client IP, applied by `RateLimitMiddleware`. `/health` is exempt.

- Sustained limit: `RATE_LIMIT_PER_MINUTE` (default `60`)
- Burst allowance: `2 ×` the per-minute limit within a 10-second window
- Response: `429` with `Retry-After`

State is **in-process**. See [AUDIT.md](AUDIT.md#a2-rate-limiting-is-per-process)
before running multiple workers.

---

# Endpoints

## `GET /health`

Unauthenticated. Component-by-component health.

```jsonc
// 200
{
  "status": "healthy",          // healthy | degraded
  "checks": {
    "database":         { "status": "ok", "detail": "MySQL connection successful" },
    "rate_limit":       { "status": "ok", "detail": "rate_limit_per_minute=60" },
    "embedding_model":  { "status": "ok", "detail": "..." },
    "vector_store":     { "status": "ok", "detail": "Qdrant client initialized" }
  }
}
```

`status: "degraded"` means at least one component failed but the process is still
serving. A failing component sets `checks.<name>.status = "degraded"`; note that
`rate_limit` only ever reports `ok` or `configurable`, never `degraded`.

## `GET /health/ready`

Unauthenticated readiness probe. `200` when the database answers, `503` otherwise.
Use this for container orchestration; use `/health` for dashboards.

## `GET /agents/health`

Authenticated. Per-agent call statistics from `core/monitoring.py`.

```jsonc
{
  "agents": {
    "llm:llama":       { "calls": 12, "successes": 12, "failures": 0,
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
| `min_salary` | float | `null` | |
| `posted_within_days` | int | `null` | |
| `sources` | string[] | `null` | Overrides `JOB_SOURCES` for this request. |

**Response** — `JobOut[]`

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
| `top_k` | int | `10` | |
| `location`, `employment_type`, `experience_level`, `remote_only`, `min_salary`, `posted_within_days` | | `null`/`false` | Same semantics as `/jobs/search`. |
| `llm_provider` | string | `null` | `auto` \| `llama` \| `gemini` \| `openrouter` \| `nvidia` \| `none` |
| `model_name` | string | `null` | |

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

Requires `FIRECRAWL_API_KEY`. Returns `422` when the key is missing. Reachability
alone is not sufficient — the page must still look like the same posting.

Without a Firecrawl key, use `GET /jobs/{job_id}` and treat `verified: false` as
"unverified", never as "dead".

---

## Errors

FastAPI's default shape:

```jsonc
{ "detail": "API key required" }              // 403
{ "detail": "Job 0f3c… not found" }           // 404
{ "detail": "FIRECRAWL_API_KEY is not configured" }  // 422
```

`422` also carries field-level validation errors when a request body is malformed.

**A failing source is not an error.** If every job source fails, `/jobs/search` returns
`200` with `[]`. Failures are logged and counted in `/agents/health`.

---

## Pagination and limits

There is **no pagination**. `/jobs/search` and `/jobs/match` return the full filtered
set. Both are bounded by `top_k` on the match path only.

This is a known limitation for large result sets — see
[AUDIT.md](AUDIT.md#a5-no-pagination).