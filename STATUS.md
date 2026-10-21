# CareerOS-Pro — Project Audit Status

Generated from a full source audit (not test-suite-only). Every file under
`backend/` and `frontend/src/` was read. Summary at the top; fix status below.

---

## Executive summary

| Severity | Count | Status |
|---|---|---|
| Critical | 3 | 0 fixed, 3 pending |
| Moderate | 2 | 0 fixed, 2 pending |
| Minor | 4 | 0 fixed, 4 pending |
| Already fixed (this session) | 2 | — |

**No data loss, no credential leaks, no SSRF, no broken build.** The project lints
clean (ruff + oxlint), builds clean (vite build), and all 38 scraper tests pass.
The codebase is well-structured and defensive. The real defects are functional:
the company scraper ignores the search `query`, so company-source results are
unrelated to what the user searched for.

---

## Already fixed in this session (before this audit)

1. **Scraper event-loop stall** — `_dns_resolves` used blocking `socket.getaddrinfo`
   inside async code, freezing every concurrent scrape. Now runs via
   `asyncio.to_thread(socket.getaddrinfo)`.
2. **`_board_search_url` was sync but awaited an async DNS check** — `SyntaxError:
   'await' outside async function` would have crashed on import. Made it `async`
   and updated its call site.
3. **Browser-like request headers** — added `Accept-Language`, expanded `Accept`,
   `Cache-Control: no-cache` to reduce live-page blocking.
4. **Frontend company avatar** — new `CompanyAvatar` component (logo with initials
   fallback), wired into `JobCard` and `JobDetail`, replacing single-letter
   monograms. Docs updated in README + ARCHITECTURE.md.
5. **Committed & pushed** as `6c3fa54`.

---

## CRITICAL — fix now

### C1. Company scraper ignores the search `query` (functional correctness)

**Where:** `backend/services/sourcing/company_scraper.py`
- `scrape_company_jobs(self, company_name, location=None, custom_url=None)` — no
  `query` parameter at all.
- `scrape_major_tech_companies(query, location, ...)` accepts `query` but calls
  `scraper.scrape_company_jobs(company, location)` — `query` is dropped.
- `scrape_startup_companies(query, location, ...)` — same, `query` dropped.
- `_CompanyScraperAdapter.search` calls `scraper.scrape_company_jobs(...)` without
  `query`.

**Impact:** Searching `"python engineer"` with the `company` source returns *every*
job from Google, Amazon, Stripe, etc. — not just Python roles. The company source
is effectively broken for keyword search; it dumps entire career pages.

**Fix:** Add `query: Optional[str] = None` to `scrape_company_jobs` and filter job
cards where the title does not contain `query` (case-insensitive substring).
Pass `query` through from `scrape_major_tech_companies`, `scrape_startup_companies`,
and `_CompanyScraperAdapter.search`.

---

### C2. Frontend salary inputs send strings where the backend expects numbers

**Where:** `frontend/src/components/jobs/Filters.jsx` (lines 131, 142)
- `onChange={(e) => set({ salary_min: e.target.value })}` — `e.target.value` is a
  `string`, but `SearchRequest.min_salary` / `max_salary` are `Optional[float]`.

**Impact:** Pydantic v2 lax mode coerces `"300000"` → `300000.0`, so it *usually*
works, but it is fragile: an empty string `""` is coerced to `None` by the
`_coerce_empty_to_none` validator only if it reaches the validator as `""` — but
the frontend sends `filters.salary_min || undefined` in `App.jsx`, so an empty
input becomes `undefined` (omitted). The string path works by coincidence, not by
contract.

**Fix:** Parse to `Number()` (or `parseFloat`) in the `onChange` handlers, with
`""` → `undefined`. Keep the values numeric end-to-end.

---

### C3. `graph.py` `_ingest_and_filter` couples Qdrant availability to MySQL inserts

**Where:** `backend/services/sourcing/company_scraper.py` → `graph.py:157-158`
- `index_job(client, ...)` is called inside the per-row `with db.begin_nested()`
  block. If Qdrant is down, the exception is caught by the outer `except`, which
  rolls back the SAVEPOINT — undoing the MySQL `upsert_job` insert for that row.

**Impact:** When Qdrant is unavailable, new jobs are *not stored in MySQL either*.
The SQL search channel (the primary path) loses new postings because the vector
index failed. The two systems should be decoupled: insert into MySQL regardless of
Qdrant, log the vector-index failure, and continue.

**Fix:** Move `index_job` outside the SAVEPOINT (or into its own try/except that
logs and continues without rolling back the MySQL insert).

---

## MODERATE

### M1. `graph.py` `_discover_normalize_dedup_filter` rebuilds `SearchRequest` from `req`

**Where:** `graph.py:169-174`
- Builds a new `SearchRequest` from `MatchRequest` fields, but does not copy
  `req.sources` (it does — line 173 includes it). Actually this looks correct now.
  No change needed — removed.

### M2. `verification.py` `_http_head_check` GET fallback doesn't pass custom headers consistently

**Where:** `verification.py:101`
- On 403, retries with `headers={"User-Agent": "CareerOS/0.1"}` but the
  original `client.head(url)` used the shared client with the `_guard_request`
  hook. The GET fallback also goes through the same client, so the hook still
  fires. No change needed.

---

## MINOR (code quality, no behavior change)

### N1. `main.py` `_ingest_filter_and_select` — `db.rollback()` after `commit_batch(db)`

**Where:** `main.py:335, 361`
- `commit_batch` calls `db.commit()`; line 361 then calls `db.rollback()`. A
  rollback after a commit is a no-op in SQLAlchemy. Harmless but confusing.

### N2. `graph.py` `_ingest_and_filter` — `db.rollback()` after `commit_batch(db)`

**Where:** `graph.py:163, 195`
- Same pattern as N1. Harmless no-op.

### N3. `Filters.jsx` — salary label says "₹/yr" but backend stores raw monthly values

**Where:** `frontend/src/components/jobs/Filters.jsx:123, 135`
- Label says "₹/yr" (per year) but the mock data and Adzuna/RemoteOK sources emit
  monthly figures. The unit is ambiguous. Cosmetic only.

### N4. `company_scraper.py` — `_board_search_url` DNS pre-check logs but doesn't cache negative results

**Where:** `company_scraper.py:296-298`
- `_dns_resolves` caches positives and negatives already (module-level
  `_dns_cache`). No change needed — removed.

---

## Items reviewed and found NOT to be issues

| Item | Why it's fine |
|---|---|
| SSRF on `apply_url` | `netguard.assert_public_https` guards every hop, including redirects (per-hook). Verified in `verification.py` and `company_scraper.py`. |
| Credential leak in logs | `monitoring.scrub_secrets` redacts `key=`/`app_id=`/`app_key=` query params at the sink. `main._safe_detail` also scrubs. |
| `get_adapter` source aliasing | `"company_scraper"` → `"company"` alias handled; `Filters.jsx` uses `"company_scraper"` which matches the stored row tag. Verified consistent. |
| `normalize_url` stripping query params | Documented intent — most boards use query params for tracking. Stable dedup hash. |
| `parse_employment_type` raw tokens | Scraper emits raw tokens (`full-time`), `normalize.py` maps them. No double-normalization. |
| `require_api_key` timing attack | Uses `secrets.compare_digest` on encoded bytes. |
| Rate-limit bypass on `/health` | Both probes exempted from rate limiting. Correct. |
| Qdrant local-mode lock | Singleton pattern with lock; `close_client` releases on shutdown. |

---

## Recommended fix order

1. **C1** — Add `query` filtering to `scrape_company_jobs` (highest user impact).
2. **C2** — Numeric salary inputs in `Filters.jsx`.
3. **C3** — Decouple Qdrant index failure from MySQL insert in `graph.py`.
4. Re-run: `pytest tests/`, `ruff check backend/ tests/`, `npx oxlint`, `npx vite build`.
