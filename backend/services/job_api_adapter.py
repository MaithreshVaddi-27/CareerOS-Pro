"""Job discovery adapters. API-first (spec 2.4).
Multi-source aggregation: dedup.py already collapses overlapping postings across
sources by url_hash/content_hash, so adding a second real API is pure upside —
more coverage, same dedup guarantee.
Company career page scraper is a secondary source that complements API coverage
with deterministic dedup — duplicates are automatically collapsed by url_hash.
"""

import asyncio
import logging
import random
import re
import time
from typing import Protocol

import httpx

from backend.core.config import get_settings
from backend.core.monitoring import agent_monitor
from backend.schemas import RawJobPosting
from backend.services.company_scraper import scrape_company_jobs

logger = logging.getLogger("careeros")
settings = get_settings()

# Use v2 endpoint per RapidAPI JSearch documentation
# NOTE: Requires active RapidAPI subscription (free tier ~200 req/month)
# Subscribe at: https://rapidapi.com/letscrape-6bRBa3QG1q/api/jsearch
JSEARCH_URL = "https://jsearch.p.rapidapi.com/search-v2"

# Reusable HTTP client for better performance
_http_client = None


def get_http_client() -> httpx.AsyncClient:
    """Get or create a reusable HTTP client."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=15.0)
    return _http_client


async def get_with_retry(
    client: httpx.AsyncClient, url: str, *, retries: int = 2, base_delay: float = 0.5, **kwargs
) -> httpx.Response:
    """GET with exponential backoff. Every adapter here was a single
    unretried request — one transient blip (a 429, a dropped connection, a
    momentary 5xx from a free-tier job board) failed that whole source for
    the request instead of just slowing it down. Retries only on the errors
    where retrying can plausibly help; a 4xx other than 429 means retrying
    would just get the same answer, so that's raised immediately.
    """
    attempt = 0
    while True:
        try:
            resp = await client.get(url, **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                resp.raise_for_status()
            return resp
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            attempt += 1
            if attempt > retries:
                raise
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
            logger.warning("retrying GET %s after error (%d/%d): %s", url, attempt, retries, exc)
            await asyncio.sleep(delay)


class JobSourceAdapter(Protocol):
    async def search(self, query: str, location: str | None = None) -> list[RawJobPosting]: ...


class MockJobAdapter:
    """Deterministic fixture data. Used when JOB_API_MODE=mock or no key configured."""

    async def search(self, query: str, location: str | None = None) -> list[RawJobPosting]:
        return [
            RawJobPosting(
                source="mock",
                source_id="mock-001",
                title=f"{query} Engineer",
                company="Example Corp",
                location_raw=location or "Remote",
                employment_type_raw="Full-time",
                description=f"Looking for a {query} engineer. Skills: Python, SQL.",
                apply_url="https://example.com/careers/mock-001?utm_source=x",
                posted_at_raw="2026-08-01",
                salary_raw="₹8,00,000 - ₹12,00,000",
            ),
            RawJobPosting(
                source="mock",
                source_id="mock-002",
                title=f"{query} Intern",
                company="Example Corp",
                location_raw=location or "Bengaluru, India",
                employment_type_raw="Internship",
                description=f"{query} internship. Skills: Python.",
                apply_url="https://example.com/careers/mock-002",
                posted_at_raw="2026-08-05",
                salary_raw="₹25,000/month",
            ),
        ]


class JSearchAdapter:
    """Real JSearch (RapidAPI) adapter. Requires JSEARCH_API_KEY."""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("JSEARCH_API_KEY required for live mode")
        self._api_key = api_key

    async def search(self, query: str, location: str | None = None) -> list[RawJobPosting]:
        # Build query with location if provided
        search_query = f"{query} in {location}" if location else query

        # Map location to country code for v2 API
        country_map = {
            "india": "in", "usa": "us", "united states": "us", "uk": "gb", "united kingdom": "gb",
            "germany": "de", "canada": "ca", "australia": "au", "france": "fr", "japan": "jp",
            "singapore": "sg", "uae": "ae", "dubai": "ae", "netherlands": "nl", "ireland": "ie",
        }
        country_code = "in"  # default to India
        if location:
            loc_lower = location.lower().strip()
            country_code = country_map.get(loc_lower, "in")

        # v2 API parameters
        params = {
            "query": search_query,
            "num_pages": "1",
            "country": country_code,
            "date_posted": "all",
        }
        headers = {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
        }
        client = get_http_client()
        resp = await get_with_retry(client, JSEARCH_URL, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        results = []
        # v2 API returns data.jobs array, not data array directly
        for item in data.get("data", {}).get("jobs", []):
            results.append(
                RawJobPosting(
                    source="jsearch",
                    source_id=item.get("job_id", ""),
                    title=item.get("job_title", ""),
                    company=item.get("employer_name", ""),
                    location_raw=item.get("job_city") or item.get("job_country") or "",
                    employment_type_raw=item.get("job_employment_type") or "",
                    description=item.get("job_description", ""),
                    apply_url=item.get("job_apply_link", ""),
                    posted_at_raw=item.get("job_posted_at_datetime_utc"),
                    salary_raw=_format_salary(item),
                )
            )
        return results


def _format_salary(item: dict) -> str | None:
    lo = item.get("job_min_salary")
    hi = item.get("job_max_salary")
    currency = item.get("job_salary_currency", "")
    if lo is None and hi is None:
        return None
    if lo and hi:
        return f"{currency} {lo}-{hi}"
    return f"{currency} {lo or hi}"


class AdzunaAdapter:
    """Real Adzuna adapter. Requires ADZUNA_APP_ID + ADZUNA_APP_KEY. Second live
    source alongside JSearch — different coverage, same dedup pipeline downstream."""

    def __init__(self, app_id: str, app_key: str, country: str):
        if not app_id or not app_key:
            raise ValueError("ADZUNA_APP_ID and ADZUNA_APP_KEY required for Adzuna")
        self._app_id = app_id
        self._app_key = app_key
        self._country = country

    async def search(self, query: str, location: str | None = None) -> list[RawJobPosting]:
        url = f"https://api.adzuna.com/v1/api/jobs/{self._country}/search/1"
        params = {
            "app_id": self._app_id,
            "app_key": self._app_key,
            "what": query,
            "content-type": "application/json",
            "results_per_page": 20,
        }
        if location:
            params["where"] = location

        client = get_http_client()
        resp = await get_with_retry(client, url, params=params)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("results", []):
            salary_raw = None
            lo, hi = item.get("salary_min"), item.get("salary_max")
            if lo or hi:
                salary_raw = f"{lo or hi}-{hi or lo}"
            results.append(
                RawJobPosting(
                    source="adzuna",
                    source_id=str(item.get("id", "")),
                    title=item.get("title", ""),
                    company=(item.get("company") or {}).get("display_name", ""),
                    location_raw=(item.get("location") or {}).get("display_name", ""),
                    employment_type_raw=item.get("contract_time", ""),
                    description=item.get("description", ""),
                    apply_url=item.get("redirect_url", ""),
                    posted_at_raw=item.get("created"),
                    salary_raw=salary_raw,
                )
            )
        return results


class RemotiveAdapter:
    """remotive.com — free, no key, remote-only listings."""

    async def search(self, query: str, location: str | None = None) -> list[RawJobPosting]:
        client = get_http_client()
        resp = await get_with_retry(client, "https://remotive.com/api/remote-jobs", params={"search": query})
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("jobs", []):
            results.append(
                RawJobPosting(
                    source="remotive",
                    source_id=str(item.get("id", "")),
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    location_raw=item.get("candidate_required_location", "Remote"),
                    employment_type_raw=item.get("job_type", ""),
                    description=item.get("description", ""),
                    apply_url=item.get("url", ""),
                    posted_at_raw=item.get("publication_date"),
                    salary_raw=item.get("salary") or None,
                )
            )
        return results


class RemoteOKAdapter:
    """remoteok.com — free, no key. First array element is a legal-notice
    object, not a job — skip it explicitly."""

    async def search(self, query: str, location: str | None = None) -> list[RawJobPosting]:
        async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": "CareerOS/0.1"}) as client:
            resp = await get_with_retry(client, "https://remoteok.com/api")
            resp.raise_for_status()
            data = resp.json()

        q_low = query.lower()
        results = []
        for item in data:
            if not isinstance(item, dict) or "id" not in item:
                continue  # skip the legal-notice header element
            title = item.get("position", "")
            tags = " ".join(item.get("tags", []))
            if q_low not in f"{title} {tags}".lower():
                continue  # RemoteOK has no server-side search — filter client-side
            results.append(
                RawJobPosting(
                    source="remoteok",
                    source_id=str(item.get("id", "")),
                    title=title,
                    company=item.get("company", ""),
                    location_raw=item.get("location", "Remote"),
                    employment_type_raw="",
                    description=item.get("description", ""),
                    apply_url=item.get("url", ""),
                    posted_at_raw=item.get("date"),
                    salary_raw=(f"{item['salary_min']}-{item['salary_max']}"
                                if item.get("salary_min") and item.get("salary_max") else None),
                )
            )
        return results


class ArbeitnowAdapter:
    """arbeitnow.com — free, no key, EU-heavy listings."""

    async def search(self, query: str, location: str | None = None) -> list[RawJobPosting]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await get_with_retry(client, "https://www.arbeitnow.com/api/job-board-api")
            resp.raise_for_status()
            data = resp.json()

        q_low = query.lower()
        results = []
        for item in data.get("data", []):
            title = item.get("title", "")
            tags = " ".join(item.get("tags", []))
            if q_low not in f"{title} {tags}".lower():
                continue  # no server-side search here either — filter client-side
            results.append(
                RawJobPosting(
                    source="arbeitnow",
                    source_id=item.get("slug", ""),
                    title=title,
                    company=item.get("company_name", ""),
                    location_raw=item.get("location", ""),
                    employment_type_raw=", ".join(item.get("job_types", [])),
                    description=item.get("description", ""),
                    apply_url=item.get("url", ""),
                    posted_at_raw=None,
                    salary_raw=None,
                )
            )
        return results


class MultiSourceAdapter:
    """Fans out to every configured live source, merges results. A single source
    failing (bad key, rate limit, outage) doesn't take down the others — logged
    and skipped, never silently pretended-successful."""

    def __init__(self, adapters: list[JobSourceAdapter]):
        self._adapters = adapters

    async def search(self, query: str, location: str | None = None) -> list[RawJobPosting]:
        results: list[RawJobPosting] = []

        async def _tracked(adapter: JobSourceAdapter) -> list[RawJobPosting]:
            name = f"source:{type(adapter).__name__}"
            start = time.monotonic()
            try:
                out = await adapter.search(query, location)
                agent_monitor.record(name, ok=True, latency_ms=(time.monotonic() - start) * 1000)
                return out
            except Exception as exc:
                agent_monitor.record(name, ok=False, latency_ms=(time.monotonic() - start) * 1000, error=str(exc)[:300])
                raise

        outcomes = await asyncio.gather(
            *(_tracked(a) for a in self._adapters), return_exceptions=True
        )
        for adapter, outcome in zip(self._adapters, outcomes):
            if isinstance(outcome, Exception):
                logger.warning("job source %s failed: %s", type(adapter).__name__, outcome)
                continue
            results.extend(outcome)
        return results


class SafeSingleSourceAdapter:
    """Wraps a single source adapter to catch exceptions gracefully.

    Instead of raising unhandled exceptions (500s), returns empty list with a warning.
    """

    def __init__(self, adapter: JobSourceAdapter):
        self._adapter = adapter

    async def search(self, query: str, location: str | None = None) -> list[RawJobPosting]:
        try:
            return await self._adapter.search(query, location)
        except Exception as e:
            logger.warning("job source %s failed: %s", type(self._adapter).__name__, e)
            return []


def get_adapter() -> JobSourceAdapter:
    settings = get_settings()
    if settings.job_api_mode != "live":
        return MockJobAdapter()

    configured: list[JobSourceAdapter] = []
    requested = {s.strip() for s in settings.job_sources.split(",") if s.strip()}

    if "jsearch" in requested and settings.jsearch_api_key:
        configured.append(JSearchAdapter(settings.jsearch_api_key))
    if "adzuna" in requested and settings.adzuna_app_id and settings.adzuna_app_key:
        configured.append(AdzunaAdapter(settings.adzuna_app_id, settings.adzuna_app_key, settings.adzuna_country))
    # no-key sources — free, always available once requested, no credential gate
    if "remotive" in requested:
        configured.append(RemotiveAdapter())
    if "remoteok" in requested:
        configured.append(RemoteOKAdapter())
    if "arbeitnow" in requested:
        configured.append(ArbeitnowAdapter())
    # company scraper — free, no key required, secondary source for broader coverage
    if "company" in requested:
        configured.append(_CompanyScraperAdapter())

    if not configured:
        import logging
        logger = logging.getLogger("careeros")
        logger.error(
            "JOB_API_MODE=live but no job sources configured with valid credentials. "
            "Configure at least one of: JSEARCH_API_KEY, ADZUNA_APP_ID/KEY, "
            "or enable free sources (remotive, remoteok, arbeitnow) in JOB_SOURCES."
        )
        raise RuntimeError(
            "No job sources configured for live mode. Set JOB_API_MODE=mock for development "
            "or configure at least one job source for live mode."
        )
    if len(configured) == 1:
        # Wrap the single source in a safe adapter so any network error / timeout
        # is caught gracefully and surfaced as an empty result (with a warning)
        # instead of an unhandled 500. MultiSourceAdapter does this for free.
        return SafeSingleSourceAdapter(configured[0])
    return MultiSourceAdapter(configured)


class _CompanyScraperAdapter:
    """Adapter wrapping the company career page scraper.

    This is a free, no-credential-required source that scrapes company career pages.
    It's intentionally placed after the key-required sources so it's used when
    no other sources have valid credentials, but can also be combined with them.
    """

    async def search(self, query: str, location: str | None = None) -> list[RawJobPosting]:
        # Try to infer company from query, or use general search
        # For now, try a few common company career page patterns
        results = []

        # Extract potential company name from query (first word capitalized)
        query_words = query.strip().split()
        if query_words:
            potential_company = query_words[0].rstrip(",;:")
            # Scrape career pages for this company
            company_results = await scrape_company_jobs(
                company_name=potential_company,
                location=location,
            )
            results.extend(company_results)

        # If no results from company scraping, try a generic approach
        if not results:
            # Try scraping some known company career pages
            company_names = await _infer_company_names_from_query(query)
            # Scrape multiple companies concurrently with limited concurrency
            semaphore = asyncio.Semaphore(settings.company_scraper_max_concurrency)
            async def _scrape_one(company_name: str) -> list[RawJobPosting]:
                async with semaphore:
                    return await scrape_company_jobs(
                        company_name=company_name,
                        location=location,
                    )
            tasks = [_scrape_one(name) for name in company_names]
            company_results = await asyncio.gather(*tasks, return_exceptions=True)
            for company_result in company_results:
                if not isinstance(company_result, Exception):
                    results.extend(company_result)
            # Fallback: scrape a short list of popular companies sequentially if no matches
            if not results:
                for company in ["Google", "Microsoft", "Apple", "Amazon", "Meta"]:
                    try:
                        company_results = await scrape_company_jobs(
                            company_name=company,
                            location=location,
                        )
                        results.extend(company_results)
                    except Exception as e:
                        logger.warning("Company scrape failed for %s: %s", company, e)
                        continue

        return results


async def _infer_company_names_from_query(query: str) -> list[str]:
    """Very light heuristic to pull potential company names from a search query."""
    # Split on common separators and capitalize each token
    raw_tokens = re.split(r"[\s_,;]+", query.lower())
    # Simple heuristic: words that look like proper nouns (first letter upper)
    candidates = [tok.capitalize() for tok in raw_tokens if tok and tok[0].isalpha()]
    # Filter out very common non-company tokens
    stop_words = {
        "remote", "full", "part", "time", "intern", "contract", "jobs", "job",
        "hiring", "careers", "career", "employment", "position", "vacancy"
    }
    return [cand for cand in candidates if cand not in stop_words][:5]
