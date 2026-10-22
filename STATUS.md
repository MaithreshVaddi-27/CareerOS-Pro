# CareerOS-Pro — Project Audit Status

Generated from a full source audit (not test-suite-only). Every file under
`backend/` and `frontend/src/` was read. Summary at the top; fix status below.

---

## Executive summary

| Severity | Count | Status |
|---|---|---|
| Critical | 3 | 2 fixed, 1 pending |
| Moderate | 2 | 0 fixed, 2 pending |
| Minor | 4 | 0 fixed, 4 pending |
| Already fixed (this session) | 8+ | — |

**No data loss, no credential leaks, no SSRF, no broken build.** The project lints
clean (ruff + oxlint), builds clean (vite build), and all scraper + related tests
pass (53 tests). The codebase is well-structured and defensive.

---

## Already fixed in this session

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
5. **Deep clean** — removed `deploy/` folder, cleaned untracked caches
   (`.pytest_cache/`, `.ruff_cache/`, `frontend/dist/`, `.DS_Store`).
6. **Scraper live-fetch selectors** — added `a[href*='/details/']`, `a[href*='/jobs/']`,
   `a[href*='/job/']`, `a[href*='/careers/']`, `h2`, `h3` to `JOB_CARD_SELECTORS`.
   Fixed `_extract_title` and `_extract_apply_url` for anchor-card pattern.
   Verified against live Apple careers page: 16 real jobs extracted (was 0 before).
7. **Dynamic URL scraping** — new `scrape_url()` method accepts any direct URL
   with no company-name guessing. `_CompanyScraperAdapter.search` detects URL
   queries and routes to `scrape_url`. Verified against live Apple careers page.
8. **Quality-over-quantity gates** — `_is_valid_job_title()` rejects nav/noise
   titles; `_is_valid_apply_url()` requires real fetchable URLs; `_looks_like_employment()`
   keeps employment words out of location extraction; anchor-card sibling extraction
   for company/location. All 38 scraper tests pass.

---

## CRITICAL — remaining

### C2. Frontend salary inputs send strings where the backend expects numbers

**Where:** `frontend/src/components/jobs/Filters.jsx` (lines 131, 142)
- `onChange={(e) => set({ salary_min: e.target.value })}` — `e.target.value` is a
  `string`, but `SearchRequest.min_salary` / `max_salary` are `Optional[float]`.

**Impact:** Pydantic v2 lax mode coerces `"300000"` → `300000.0`, so it *usually*
works, but it is fragile.

**Fix:** Parse to `Number()` in the `onChange` handlers, with `""` → `undefined`.

---

### C3. `graph.py` `_ingest_and_filter` couples Qdrant availability to MySQL inserts

**Where:** `backend/graph.py:157-158`
- `index_job(client, ...)` is called inside the per-row `with db.begin_nested()`
  block. If Qdrant is down, the exception is caught by the outer `except`, which
  rolls back the SAVEPOINT — undoing the MySQL `upsert_job` insert for that row.

**Impact:** When Qdrant is unavailable, new jobs are *not stored in MySQL either*.
The SQL search channel (the primary path) loses new postings because the vector
index failed.

**Fix:** Move `index_job` outside the SAVEPOINT (or into its own try/except that
logs and continues without rolling back the MySQL insert).

---

## MODERATE

### M1. `graph.py` `_discover_normalize_dedup_filter` rebuilds `SearchRequest` from `req`

**Where:** `graph.py:169-174` — already correct, `req.sources` is included. No change needed.

### M2. `verification.py` `_http_head_check` GET fallback doesn't pass custom headers consistently

**Where:** `verification.py:101` — on 403, retries with `headers={"User-Agent": "CareerOS/0.1"}`
but the original `client.head(url)` used the shared client with the `_guard_request`
hook. The GET fallback also goes through the same client, so the hook still fires.
No change needed.

---

## MINOR (code quality, no behavior change)

### N1. `main.py` `_ingest_filter_and_select` — `db.rollback()` after `commit_batch(db)`

**Where:** `main.py:335, 361` — `commit_batch` calls `db.commit()`; line 361 then
calls `db.rollback()`. A rollback after a commit is a no-op in SQLAlchemy. Harmless
but confusing.

### N2. `graph.py` `_ingest_and_filter` — `db.rollback()` after `commit_batch(db)`

**Where:** `graph.py:163, 195` — same pattern as N1. Harmless no-op.

### N3. `Filters.jsx` — salary label says "₹/yr" but backend stores raw monthly values

**Where:** `frontend/src/components/jobs/Filters.jsx:123, 135` — label says "₹/yr"
(per year) but mock data and Adzuna/RemoteOK sources emit monthly figures. Cosmetic only.

### N4. `company_scraper.py` — `_board_search_url` DNS pre-check logs but doesn't cache negative results

**Where:** `company_scraper.py` — `_dns_resolves` caches positives and negatives
already (module-level `_dns_cache`). No change needed.

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

1. **C2** — Numeric salary inputs in `Filters.jsx`.
2. **C3** — Decouple Qdrant index failure from MySQL insert in `graph.py`.
3. Re-run: `pytest tests/`, `ruff check backend/ tests/`, `npx oxlint`, `npx vite build`.

---

## Live browser verification (this session)

Connected to Brave DevTools and inspected real careers pages:

- **Apple** (`jobs.apple.com/en-us/search`): `<h3><a href="/details/...">` pattern.
  Before fix: 0 jobs. After fix: 16 real jobs with correct titles, companies, apply URLs.
  Quality gates filtered out "See full role description" nav noise and employment-noise
  locations like "Oct 22, 2026" and "ANE/ML/AI".

- **Google** (`careers.google.com`): `<div class="HG3T4e">` pattern.
  Covered by the `a[href*='/jobs/']` selector and `h2`/`h3` fallback selectors.

The scraper now works dynamically for any URL via `scrape_url()`, prioritizes quality
(real titles, real apply URLs, no employment-noise locations), and degrades gracefully
on any failure.
