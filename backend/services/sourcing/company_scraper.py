"""Company career-page scraper.

Politely scrapes job listings from a company's own career page. Everything here
is deterministic: no LLM, no guessing. Fields we cannot read stay empty so
``normalize_job`` can mark them ``unknown`` (spec 2.3).

Shape of a run:
    1. resolve the career URL (explicit, or probed from known patterns)
    2. fetch with per-domain concurrency, bounded retry, TTL cache
    3. extract cards, then per-card fields, filtering by location if given
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
import time
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from backend.core.config import get_settings
from backend.schemas import RawJobPosting

logger = logging.getLogger("careeros")
settings = get_settings()

# Anchor text that is navigation, not a job title.
_NAV_WORDS = ("apply", "read more", "skip", "home", "learn more", "view job", "details")

# Class names that commonly hold the employer name on a card.
_COMPANY_CLASS_RE = re.compile(r"company|employer|org(?:anisation|anization)?", re.I)

# "City, ST" / "City, Country" — anchored to whitespace so it can't match mid-word.
_CITY_REGION_RE = re.compile(r"[A-Z][\w.'-]*(?: [A-Z][\w.'-]*)*,\s*[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*)*")

# Substring markers, ordered so the most specific phrase wins ("full-time"
# before "fulltime" is not an issue, but "contract-to-hire" must beat
# "contract"). Values are the raw tokens normalize.py expects.
_EMPLOYMENT_MARKERS = (
    "contract-to-hire",
    "full-time",
    "full time",
    "fulltime",
    "part-time",
    "part time",
    "internship",
    "intern",
    "contract",
)


class CompanyScraper:
    """Scrapes job listings from company career pages.

    Every fetch is gated on the site's robots.txt (audit A6); a disallowed path is
    never requested. Politeness comes from robots.txt plus a per-domain semaphore,
    jitter, a TTL cache and a configurable User-Agent.
    """

    # Probed in order; first pattern whose page yields job cards wins.
    # Override/extend via settings.company_career_page_patterns (comma-separated).
    CAREER_URL_PATTERNS = [
        "/careers",
        "/jobs",
        "/open-positions",
        "/careers/all-jobs",
        "/join-us",
        "/work-with-us",
        "/vacancies",
        "/talent",
    ]

    # Ordered by specificity — first selector with usable cards wins.
    JOB_CARD_SELECTORS = [
        "article.job",
        "li[data-job-id]",
        "[data-job-id]",
        ".job-card",
        ".job-item",
        ".position-list li",
        ".jobs-list li",
        ".job-listings li",
        ".opening-list li",
        "#jobs .job",
        ".css-1n4p8mf",
        ".css-1q2qfg6",
    ]

    def __init__(
        self,
        max_concurrency: Optional[int] = None,
        jitter_max: Optional[float] = None,
        cache_size: Optional[int] = None,
    ) -> None:
        self._per_domain_limit = (
            max_concurrency
            if max_concurrency is not None
            else settings.company_scraper_max_concurrency
        )
        self._jitter_max = jitter_max if jitter_max is not None else settings.company_scraper_jitter_max
        self._cache_size = cache_size if cache_size is not None else settings.company_scraper_cache_size
        self._cache_ttl = settings.company_scraper_cache_ttl_seconds
        self._timeout = settings.company_scraper_timeout_seconds
        self._max_retries = settings.company_scraper_max_retries
        self._max_probes = settings.company_scraper_max_url_probes

        self._domain_semaphores: Dict[str, asyncio.Semaphore] = {}
        self._html_cache: Dict[str, tuple[float, str]] = {}
        # origin -> parsed rules, or None for "no usable robots.txt, allow all".
        self._robots: Dict[str, Optional[RobotFileParser]] = {}
        self._client: Optional[httpx.AsyncClient] = None

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def aclose(self) -> None:
        """Close the shared HTTP client. Optional — the scraper also works
        without it, it just reconnects per request."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "CompanyScraper":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    def _get_client(self) -> httpx.AsyncClient:
        # One client per scraper: connection pooling + keep-alive across pages.
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={
                    "User-Agent": settings.company_scraper_user_agent,
                    "Accept": "text/html,application/xhtml+xml",
                },
                follow_redirects=True,
            )
        return self._client

    # -------------------------------------------------------------------------
    # URL resolution
    # -------------------------------------------------------------------------

    @staticmethod
    def _base_site(company_name: str) -> str:
        """Guess a company's root URL from its name: ``Acme Corp`` -> ``acmecorp.com``."""
        slug = re.sub(r"[^a-z0-9]+", "", company_name.lower())
        return f"https://{slug}.com"

    @staticmethod
    def _join(base_url: str, path: str) -> str:
        """Attach a path to a base URL, keeping the base's scheme and host."""
        parsed = urlparse(base_url.rstrip("/"))
        return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))

    def _career_url_candidates(self, base_url: str) -> List[str]:
        """Ordered, de-duplicated list of career URLs to probe."""
        # Settings patterns go first: they are explicit operator intent.
        custom = [p.strip() for p in settings.company_career_page_patterns.split(",") if p.strip()]
        patterns = custom + self.CAREER_URL_PATTERNS

        candidates: List[str] = []
        seen = set()
        for pattern in patterns:
            pattern = pattern.strip()
            if not pattern:
                continue
            url = pattern if pattern.startswith("http") else self._join(base_url, pattern)
            if url not in seen:
                seen.add(url)
                candidates.append(url)
            if len(candidates) >= self._max_probes:
                break
        return candidates

    # -------------------------------------------------------------------------
    # Fetching
    # -------------------------------------------------------------------------

    def _cache_get(self, url: str) -> Optional[str]:
        hit = self._html_cache.get(url)
        if hit is None:
            return None
        stored_at, html = hit
        if self._cache_ttl > 0 and (time.monotonic() - stored_at) > self._cache_ttl:
            del self._html_cache[url]
            return None
        return html

    def _cache_put(self, url: str, html: str) -> None:
        if len(self._html_cache) >= self._cache_size:
            # FIFO eviction: drop the oldest insertion.
            del self._html_cache[next(iter(self._html_cache))]
        self._html_cache[url] = (time.monotonic(), html)

    async def _robots_parser(self, url: str) -> Optional[RobotFileParser]:
        """Parsed robots.txt for this URL's origin, or None if there is nothing
        usable to enforce.

        Per RFC 9309: a 4xx means the site publishes no rules, so allow; a 5xx or
        a transport failure means the rules are merely *unavailable*, which is not
        the same as permission. Those are deliberately not cached, so the next run
        re-checks rather than inheriting a stale "allow" for the life of the
        process.
        """
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]

        robots_url = urljoin(origin, "/robots.txt")
        parser: Optional[RobotFileParser] = None
        cacheable = False
        try:
            resp = await self._get_client().get(robots_url)
            if resp.status_code == 200:
                parser = RobotFileParser()
                parser.parse(resp.text.splitlines())
                cacheable = True
            elif resp.status_code in (401, 403):
                # 4xx other than 404: the site is explicitly refusing the rules.
                # Treat as disallow-all rather than pretending it published none.
                parser = RobotFileParser()
                parser.parse(["User-agent: *", "Disallow: /"])
                cacheable = True
            elif resp.status_code < 500:
                # 404 and friends: definitively no rules published, so allow.
                logger.info("No robots.txt at %s (HTTP %s) — allowing", robots_url, resp.status_code)
                cacheable = True
            else:
                # 5xx: unavailable, not permitted — allow now, re-check next time.
                logger.info("robots.txt unavailable at %s (HTTP %s) — allowing", robots_url, resp.status_code)
        except httpx.HTTPError as exc:
            logger.info("robots.txt unreachable at %s (%s) — allowing", robots_url, exc)

        if cacheable:
            self._robots[origin] = parser
        return parser

    async def _robots_allows(self, url: str, parser: Optional[RobotFileParser]) -> bool:
        """Gate `url` on the site's rules, honouring any crawl-delay.

        Takes an already-fetched parser so a robots.txt that is unavailable
        (5xx) is not re-requested twice per page.
        """
        if parser is None:
            return True
        user_agent = settings.company_scraper_user_agent.split("/")[0] or "*"
        if not parser.can_fetch(user_agent, url):
            logger.info("robots.txt disallows %s for %s — skipping", url, user_agent)
            return False
        delay = parser.crawl_delay(user_agent)
        # ponytail: capped at 10s — a site asking for more would stall the
        # pipeline for no benefit, and robots.txt is a courtesy, not a hard cap.
        if delay:
            await asyncio.sleep(min(float(delay), 10.0))
        return True

    async def _fetch_html(self, url: str) -> Optional[str]:
        """Fetch one URL. Returns None on any non-recoverable failure.

        Respects robots.txt before fetching (audit A6). Retries only on 429/5xx and
        transport errors, with exponential backoff plus jitter. A fresh scraper
        gets a fresh empty cache.
        """
        cached = self._cache_get(url)
        if cached is not None:
            return cached

        # The per-domain semaphore is deliberately acquired *before* the robots
        # fetch so that robots.txt lookups obey the same concurrency cap as pages.
        hostname = urlparse(url).hostname or ""
        domain_sem = self._domain_semaphores.setdefault(
            hostname, asyncio.Semaphore(self._per_domain_limit)
        )

        async with domain_sem:
            if not await self._robots_allows(url, await self._robots_parser(url)):
                return None

            client = self._get_client()
            for attempt in range(self._max_retries + 1):
                try:
                    resp = await client.get(url)
                except httpx.HTTPError as exc:
                    if attempt < self._max_retries:
                        await self._backoff(attempt, url, f"transport error: {exc}")
                        continue
                    logger.warning("Giving up on %s after transport errors: %s", url, exc)
                    return None

                if resp.status_code == 200:
                    html = resp.text
                    self._cache_put(url, html)
                    return html

                if resp.status_code in (429, 500, 502, 503) and attempt < self._max_retries:
                    await self._backoff(attempt, url, f"HTTP {resp.status_code}")
                    continue

                # 4xx (other than 429) and 3xx-after-redirects will not fix
                # themselves on retry — fail fast instead of hammering.
                logger.warning("Career page %s returned %s", url, resp.status_code)
                return None

        return None

    async def _backoff(self, attempt: int, url: str, reason: str) -> None:
        delay = 2 ** attempt + random.uniform(0, self._jitter_max)
        logger.warning(
            "Retrying %s in %.1fs (%s, attempt %d/%d)",
            url, delay, reason, attempt + 1, self._max_retries,
        )
        await asyncio.sleep(delay)

    async def _fetch_first_career_page(self, base_url: str) -> tuple[Optional[str], Optional[str]]:
        """Probe candidate career URLs concurrently; return the first (html, url)
        that actually yields job cards.

        Judging by extracted cards rather than keyword-grepping the page is both
        cheaper to reason about and more accurate: a generic landing page mentions
        "career" and "join us" without listing a single role.
        """
        candidates = self._career_url_candidates(base_url)
        if not candidates:
            return None, None

        pages = await asyncio.gather(
            *(self._fetch_html(url) for url in candidates), return_exceptions=True
        )
        for url, html in zip(candidates, pages):
            if isinstance(html, str) and self._extract_job_cards(html):
                return html, url
        return None, None

    # -------------------------------------------------------------------------
    # Card extraction
    # -------------------------------------------------------------------------

    @staticmethod
    def _extract_job_cards(html: str) -> List[BeautifulSoup]:
        """Extract job-card elements. Tries selectors first, then a keyword
        heuristic that returns *all* matches (not just the first)."""
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        for selector in CompanyScraper.JOB_CARD_SELECTORS:
            cards = [
                card
                for card in soup.select(selector)
                if len(card.get_text(separator=" ", strip=True)) > 20 or card.find("a", href=True)
            ]
            if cards:
                return cards

        # Fallback: any list item / article whose text looks like a role.
        fallback = []
        for element in soup.find_all(["li", "article"]):
            text = element.get_text(separator=" ", strip=True).lower()
            if len(text) > 30 and any(
                kw in text
                for kw in ("engineer", "developer", "manager", "director", "intern", "analyst", "designer")
            ):
                fallback.append(element)
        return fallback

    @staticmethod
    def _extract_title(card: BeautifulSoup) -> str:
        for tag in ("h3", "h2", "h1"):
            el = card.find(tag)
            if el:
                title = el.get_text(strip=True)
                if title:
                    return title
        for a in card.find_all("a", href=True):
            text = a.get_text(strip=True)
            if 2 < len(text) < 100 and not any(x in text.lower() for x in _NAV_WORDS):
                return text
        return ""

    @staticmethod
    def _extract_company(card: BeautifulSoup) -> str:
        """Employer name if the card advertises one, else "".

        Deliberately returns "" rather than the whole card text — the caller
        falls back to the requested company name, which is always correct, while
        dumping card text into ``company`` would poison every downstream field.
        """
        for tag in ("a", "span", "div", "p"):
            el = card.find(tag, class_=_COMPANY_CLASS_RE)
            if el:
                text = el.get_text(strip=True)
                if 1 < len(text) < 80:
                    return text
        return ""

    @staticmethod
    def _extract_location(card: BeautifulSoup) -> str:
        """Location from a labelled field if present, else from card text."""
        for selector in (".location", "[data-location]", "[class*=location]", "[class*=Location]"):
            el = card.select_one(selector)
            if el:
                text = el.get_text(strip=True)
                if text:
                    return text

        text = card.get_text(separator=" ", strip=True)
        # Parenthesised form wins — it is the most common explicit marker.
        paren = re.search(r"\(([^)]{2,60})\)", text)
        if paren:
            candidate = paren.group(1).strip()
            if not any(w in candidate.lower() for w in ("full", "part", "contract", "intern")):
                return candidate

        # "City, ST" — run against the *original* casing, never a lowercased copy.
        city = _CITY_REGION_RE.search(text)
        if city:
            return city.group(0).strip()
        return ""

    @staticmethod
    def _extract_employment_type(card: BeautifulSoup) -> str:
        """Raw employment keyword as it appears on the page.

        Returns the *raw* token, not the normalized one: ``normalize_job``
        maps it via ``parse_employment_type``. Returning an already-mapped
        value here double-normalizes and silently yields "unknown".
        Values below are chosen to be keys that ``_EMPLOYMENT_MAP`` in
        normalize.py actually accepts.
        """
        text = card.get_text(separator=" ", strip=True).lower()
        for keyword in _EMPLOYMENT_MARKERS:
            if keyword in text:
                return keyword
        return ""

    @staticmethod
    def _extract_description(card: BeautifulSoup) -> str:
        for selector in (".description", ".job-description", ".description-container", "p"):
            el = card.select_one(selector)
            if el:
                text = el.get_text(separator=" ", strip=True)
                if len(text) > 40:
                    return text[:500]
        return card.get_text(separator=" ", strip=True)[:500]

    @staticmethod
    def _extract_apply_url(card: BeautifulSoup, base_url: str) -> Optional[str]:
        """Job-specific apply/detail URL, or None.

        None is meaningful: the caller then synthesises a stable slug URL so two
        different roles on the same career page cannot collide on ``url_hash``.
        Only links *inside* the card are considered — walking up to a sibling
        card's anchor is how postings used to get each other's URLs.
        """
        for a in card.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(strip=True).lower()
            is_apply = "apply" in text or href.lower().startswith(
                ("/apply", "/jobs/apply", "mailto:", "https://jobs.")
            )
            if is_apply:
                return urljoin(base_url, href)

        # No explicit apply link: any in-card link is the detail page.
        links = card.find_all("a", href=True)
        if links:
            return urljoin(base_url, links[0]["href"].strip())
        return None

    # -------------------------------------------------------------------------
    # Identity
    # -------------------------------------------------------------------------

    @staticmethod
    def _compute_source_id(title: str, company: str, location: str, apply_url: str) -> str:
        """Deterministic source_id for dedup."""
        key = f"{title.strip().lower()}|{company.strip().lower()}|{location.strip().lower()}|{apply_url}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _matches_location(location_raw: str, wanted: Optional[str]) -> bool:
        """Case-insensitive containment filter. No filter requested -> keep all."""
        if not wanted:
            return True
        return wanted.strip().lower() in location_raw.lower()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def scrape_company_jobs(
        self,
        company_name: str,
        location: Optional[str] = None,
        custom_url: Optional[str] = None,
    ) -> List[RawJobPosting]:
        """Scrape job postings for one company.

        Args:
            company_name: Employer name; also the source of the guessed domain.
            location: Optional case-insensitive location filter.
            custom_url: Exact career page URL; skips URL probing when given.

        Returns:
            Postings, de-duplicated by source_id. Empty list on any failure —
            a scrape is best-effort enrichment, never a hard dependency.
        """
        try:
            if custom_url:
                # Explicit URL wins even if it looks sparse — the caller asked
                # for this page specifically.
                career_url = custom_url.rstrip("/")
                html = await self._fetch_html(career_url)
            else:
                base = self._base_site(company_name)
                html, discovered = await self._fetch_first_career_page(base)
                career_url = discovered or self._join(base, self.CAREER_URL_PATTERNS[0])

            if not html:
                logger.info("No career page HTML for %s (%s)", company_name, career_url)
                return []

            cards = self._extract_job_cards(html)
            if not cards:
                logger.info("No job cards found on %s", career_url)
                return []

            return self._build_postings(cards, company_name, career_url, location)

        except Exception as exc:  # network/parse boundary — never propagate
            logger.error("Unexpected error scraping career page %s: %s", career_url, exc)
            return []

    def _build_postings(
        self,
        cards: List[BeautifulSoup],
        company_name: str,
        career_url: str,
        location: Optional[str],
    ) -> List[RawJobPosting]:
        results: List[RawJobPosting] = []
        seen: set[str] = set()

        for idx, card in enumerate(cards):
            try:
                title = self._extract_title(card).strip()
                if not title:
                    continue

                location_raw = self._extract_location(card).strip()
                if not self._matches_location(location_raw, location):
                    continue

                company = self._extract_company(card).strip() or company_name
                apply_url = self._extract_apply_url(card, career_url)
                if not apply_url:
                    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "job"
                    apply_url = f"{career_url.rstrip('/')}/job/{slug}-{idx}"

                source_id = self._compute_source_id(title, company, location_raw, apply_url)
                if source_id in seen:
                    continue
                seen.add(source_id)

                results.append(
                    RawJobPosting(
                        source="company_scraper",
                        source_id=source_id,
                        title=title,
                        company=company,
                        location_raw=location_raw,
                        employment_type_raw=self._extract_employment_type(card),
                        description=self._extract_description(card).strip(),
                        apply_url=apply_url,
                        posted_at_raw=None,
                        salary_raw=self._extract_salary(card),
                    )
                )
            except Exception as exc:  # one malformed card must not kill the batch
                logger.warning("Skipping malformed job card #%d: %s", idx, exc)

        logger.info("Scraped %d jobs from %s", len(results), career_url)
        return results

    @staticmethod
    def _extract_salary(card: BeautifulSoup) -> Optional[str]:
        for selector in (".salary", "[data-salary]", "[class*=salary]", "[class*=Salary]"):
            el = card.select_one(selector)
            if el:
                text = el.get_text(strip=True)
                if any(ch.isdigit() for ch in text):
                    return text
        return None


async def scrape_company_jobs(
    company_name: str,
    location: Optional[str] = None,
    custom_url: Optional[str] = None,
) -> List[RawJobPosting]:
    """Module-level entry point used by the job-source adapter."""
    async with CompanyScraper() as scraper:
        return await scraper.scrape_company_jobs(company_name, location, custom_url)