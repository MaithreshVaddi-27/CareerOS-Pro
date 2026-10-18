"""Firecrawl verification pass (spec: 'selective verification', previously deferred).

Confirms a job posting is actually live at its apply_url before it's shown as
verified — cheap, deterministic checks first (HTTP status), Firecrawl scrape
only when we need rendered/JS content or a company-page cross-check. Never
invents a verification result — failure to reach Firecrawl or the URL means
verified stays False, not True (spec 2.3: unknown/false, never guessed).
"""

import logging

import httpx
from pydantic import BaseModel, ValidationError

from backend.core.config import get_settings
from backend.core.netguard import BlockedTarget, assert_public_https, is_public_https

logger = logging.getLogger("careeros.verification")

settings = get_settings()

FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"

# Reusable HTTP clients for better performance
_http_client = None
_firecrawl_client = None


async def _guard_request(request: httpx.Request) -> None:
    """httpx request hook — fires once per hop, redirects included.

    `apply_url` is ingested verbatim from third-party feeds anyone can post to
    (RemoteOK, Arbeitneed, ...), so this is an attacker-reachable SSRF target
    against the metadata service and every container on careeros-net.

    Must be async: httpx awaits hooks on AsyncClient.
    """
    assert_public_https(str(request.url))


def get_http_client() -> httpx.AsyncClient:
    """Get or create a reusable HTTP client."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            event_hooks={"request": [_guard_request]},
        )
    return _http_client


def get_firecrawl_client() -> httpx.AsyncClient:
    """Get or create a reusable Firecrawl HTTP client."""
    global _firecrawl_client
    if _firecrawl_client is None:
        _firecrawl_client = httpx.AsyncClient(timeout=30.0)
    return _firecrawl_client


class VerificationResult(BaseModel):
    verified: bool
    reason: str
    # Whether the page positively states the posting is closed/filled. This is a
    # structured signal, not something to re-derive by substring-matching `reason`
    # — the caller uses it to deactivate the row, and `reason` is human-readable
    # prose that gets reworded. See /jobs/{id}/verify.
    posting_closed: bool = False
    page_title: str | None = None
    content_snippet: str | None = None


class FirecrawlResponse(BaseModel):
    """Firecrawl v2 /scrape response structure. Validated before trusting content."""
    success: bool
    data: dict | None = None


class FirecrawlData(BaseModel):
    markdown: str | None = None
    metadata: dict | None = None


async def _http_head_check(url: str) -> bool:
    """Check URL reachability via HEAD, with graceful 403 handling.

    On 403 (forbidden), falls back to GET with a proper User-Agent header
    before giving up, since some ATS platforms block HEAD but allow GET.
    """
    # Checked up front too, so a blocked target is a clean "not reachable"
    # rather than an exception escaping from inside the hook.
    if not is_public_https(url):
        return False
    client = get_http_client()
    try:
        resp = await client.head(url)
        if resp.status_code == 405:  # some ATS boards reject HEAD, fall back to GET
            resp = await client.get(url)
            return resp.status_code < 400
        if resp.status_code == 403:  # forbidden — try GET with User-Agent
            resp = await client.get(url, headers={"User-Agent": "CareerOS/0.1"})
            return resp.status_code < 400
        return resp.status_code < 400
    except BlockedTarget as exc:
        # A redirect hop pointed somewhere we refuse.
        logger.warning("blocked redirect target while verifying: %s", exc)
        return False
    except httpx.HTTPError as exc:
        logger.info("HEAD check failed for apply_url: %s", exc)
        return False


async def _firecrawl_scrape(url: str) -> FirecrawlResponse | None:
    if not settings.firecrawl_api_key:
        return None
    headers = {"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"}
    payload = {"url": url, "formats": ["markdown"], "onlyMainContent": True}
    try:
        client = get_firecrawl_client()
        resp = await client.post(FIRECRAWL_SCRAPE_URL, headers=headers, json=payload)
        resp.raise_for_status()
        return FirecrawlResponse.model_validate(resp.json())
    except (httpx.HTTPError, ValidationError) as e:
        # Warning, not debug: at the root INFO level this never emitted, so a bad
        # key or exhausted quota looked identical to an empty page — and the
        # caller returns the same verified=False either way.
        logger.warning("firecrawl scrape failed: %s", e)
        return None


def _looks_like_dead_posting(markdown: str) -> bool:
    low = markdown.lower()
    dead_markers = ("position has been filled", "no longer accepting applications",
                    "job not found", "posting has expired", "this job is closed")
    return any(m in low for m in dead_markers)


async def verify_job_posting(apply_url: str, expected_title: str, expected_company: str) -> VerificationResult:
    """Two-stage: cheap reachability check, then Firecrawl content check if configured."""
    reachable = await _http_head_check(apply_url)
    if not reachable:
        return VerificationResult(verified=False, reason="apply_url not reachable")

    if not settings.firecrawl_api_key:
        # reachable but unconfirmed content — honest partial result, not a guess
        return VerificationResult(verified=False, reason="reachable, no FIRECRAWL_API_KEY to confirm content")

    try:
        fc_resp = await _firecrawl_scrape(apply_url)
    except httpx.HTTPError as e:
        return VerificationResult(verified=False, reason=f"firecrawl request failed: {e}")

    if not fc_resp or not fc_resp.success or not fc_resp.data:
        return VerificationResult(verified=False, reason="firecrawl returned no usable content")

    try:
        fc_data = FirecrawlData.model_validate(fc_resp.data)
    except ValidationError:
        return VerificationResult(verified=False, reason="firecrawl response could not be parsed")
    markdown = fc_data.markdown or ""
    page_title = (fc_data.metadata or {}).get("title")

    if _looks_like_dead_posting(markdown):
        return VerificationResult(
            verified=False, reason="posting appears closed/filled", posting_closed=True,
            page_title=page_title, content_snippet=markdown[:280],
        )

    low_md = markdown.lower()
    title_present = expected_title.lower() in low_md if expected_title else True
    company_present = expected_company.lower() in low_md if expected_company else True

    if title_present and company_present:
        return VerificationResult(
            verified=True, reason="reachable and content matches expected title/company",
            page_title=page_title, content_snippet=markdown[:280],
        )

    return VerificationResult(
        verified=False, reason="reachable but title/company not confirmed in page content",
        page_title=page_title, content_snippet=markdown[:280],
    )
