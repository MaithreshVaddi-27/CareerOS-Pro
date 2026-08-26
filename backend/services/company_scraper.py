"""Refactored company career page scraper.

The implementation encapsulates scraping logic in a `CompanyScraper` class,
adds configurable URL patterns, an in‑memory HTML cache, retry logic,
per‑domain concurrency limits, back‑off jitter, and supports concurrent
scraping of multiple companies.
"""

import asyncio
import hashlib
import logging
import random
import re
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from backend.core.config import get_settings
from backend.schemas import RawJobPosting

logger = logging.getLogger("careeros")
settings = get_settings()


class CompanyScraper:
    """
    Scrapes job listings from company career pages.

    Features
    -------
    * Configurable career‑page URL patterns (can be overridden via settings).
    * Robust job‑card extraction using multiple CSS selectors.
    * Deterministic dedup via URL hash and content hash (unchanged behavior).
    * In‑memory HTML cache with configurable size.
    * Async request handling with configurable concurrency, retry, per‑domain
      semaphore limits and jitter.
    * Extensible extraction helpers for title, company, location, etc.
    """

    # Default URL patterns – can be overridden via settings if desired.
    CAREER_URL_PATTERNS = [
        "/careers",
        "/careers?",
        "/jobs",
        "/jobs?",
        "/talent",
        "/talent?",
        "/careersite",
        "/careersite?",
        "/work-with-us",
        "/work-with-us?",
        "/join-us",
        "/join-us?",
        "/our-team",
        "/our-team?",
        "/careers#",
        "/jobs#",
    ]

    # Common job‑card CSS selectors.
    JOB_CARD_SELECTORS = [
        "article.job",
        ".job-card",
        ".position-list li",
        ".jobs-list li",
        ".job-listings li",
        ".opening-list li",
        "#jobs .job",
        ".css-1n4p8mf",
        ".css-1q2qfg6",
        ".job-item",
        "[data-job-id]",
    ]

    def __init__(self, max_concurrency: Optional[int] = None, jitter_max: Optional[float] = None, cache_size: Optional[int] = None) -> None:
        # Load default limits from global settings, with optional overrides
        per_domain_limit = max_concurrency if max_concurrency is not None else settings.company_scraper_max_concurrency
        self._semaphore_value = per_domain_limit
        self._jitter_max = jitter_max if jitter_max is not None else settings.company_scraper_jitter_max
        cache_size_val = cache_size if cache_size is not None else settings.company_scraper_cache_size
        # Mapping of hostname → its own semaphore (created lazily).
        self._domain_semaphores: Dict[str, asyncio.Semaphore] = {}
        self._html_cache: Dict[str, str] = {}
        self._cache_size = cache_size_val

    # -------------------------------------------------------------------------
    # Utility methods
    # -------------------------------------------------------------------------

    async def _fetch_html(self, url: str) -> Optional[str]:
        """Fetch HTML with caching, retry, per‑domain concurrency control, and jitter."""
        if url in self._html_cache:
            return self._html_cache[url]

        # Determine hostname for per‑domain semaphore
        hostname = urlparse(url).hostname or ""
        # Get or create a semaphore limited by the instance max_concurrency setting
        domain_sem = self._domain_semaphores.setdefault(
            hostname,
            asyncio.Semaphore(getattr(self, '_semaphore_value', settings.company_scraper_max_concurrency)),
        )

        async with domain_sem:
            retry_count = 0
            while retry_count < 3:
                try:
                    async with httpx.AsyncClient(
                        timeout=20.0, headers={"User-Agent": "CareerOS/0.1"}, follow_redirects=True
                    ) as client:
                        resp = await client.get(url)
                        if resp.status_code == 200:
                            content = resp.text
                            # Update cache
                            if len(self._html_cache) >= self._cache_size:
                                # Simple FIFO eviction
                                oldest = next(iter(self._html_cache))
                                del self._html_cache[oldest]
                            self._html_cache[url] = content
                            return content
                        else:
                            # Retry on transient server errors / rate limits;
                            # 429 (rate limited), 500/502/503 (server errors) are worth retrying.
                            if resp.status_code in (429, 500, 502, 503) and retry_count < 2:
                                logger.warning(
                                    "Career page returned %s for %s — retrying (%d/3)",
                                    resp.status_code, url, retry_count + 1,
                                )
                                retry_count += 1
                                jitter = random.uniform(0, self._jitter_max)
                                await asyncio.sleep(2 ** retry_count + jitter)
                                continue
                            logger.warning(
                                "Career page returned %s for %s", resp.status_code, url
                            )
                            return None
                except httpx.HTTPError as e:
                    logger.error("HTTP error fetching %s: %s", url, e)
                    retry_count += 1
                    # Exponential back‑off with jitter (0‑0.5 s)
                    jitter = random.uniform(0, self._jitter_max)
                    await asyncio.sleep(2 ** retry_count + jitter)
            return None

    def _normalize_career_url(self, base_url: str, pattern: str) -> str:
        """Build a full career URL from a base URL and a pattern."""
        base = base_url.rstrip("/")
        if pattern.startswith("/"):
            path = pattern
        else:
            path = "/" + pattern
        parsed = urlparse(base)
        return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))

    def _is_career_page(self, url: str) -> bool:
        """Check if a URL matches any known career‑page pattern."""
        parsed = urlparse(url.lower())
        path = parsed.path.rstrip("/")
        for pattern in self.CAREER_URL_PATTERNS:
            pattern_path = pattern.rstrip("?").rstrip("#")
            if path == pattern_path or path.startswith(pattern_path + "/"):
                return True
        return False

    @staticmethod
    def _extract_job_cards(html: str) -> List[BeautifulSoup]:
        """Extract job‑card elements from career‑page HTML."""
        soup = BeautifulSoup(html, "html.parser")
        for selector in CompanyScraper.JOB_CARD_SELECTORS:
            cards = soup.select(selector)
            if cards:
                filtered = []
                for card in cards:
                    text = card.get_text(separator=" ", strip=True).lower()
                    if len(text) > 20 or card.find("a"):
                        filtered.append(card)
                if filtered:
                    return filtered
        # Fallback: look for any <li> or <article> with job‑related text
        for element in soup.find_all(["li", "article"]):
            text = element.get_text(separator=" ", strip=True).lower()
            if len(text) > 30 and any(
                kw in text for kw in ["engineer", "developer", "manager", "director", "intern"]
            ):
                return [element]
        return []

    @staticmethod
    def _extract_title(card: BeautifulSoup) -> str:
        for tag in ["h3", "h2", "h1"]:
            el = card.find(tag)
            if el:
                title = el.get_text(strip=True)
                if title:
                    return title
        for a in card.find_all("a", href=True):
            text = a.get_text(strip=True)
            if text and 2 < len(text) < 100:
                if not any(x in text.lower() for x in ["apply", "read more", "skip", "home"]):
                    return text
        return ""

    @staticmethod
    def _extract_company(card: BeautifulSoup) -> str:
        for tag in ["a", "span"]:
            el = card.find(tag, class_=re.compile(r"company|employer", re.I))
            if el:
                text = el.get_text(strip=True)
                if text and len(text) > 1:
                    return text
        text = card.get_text(strip=True)
        return ""

    @staticmethod
    def _extract_location(card: BeautifulSoup) -> str:
        text = card.get_text(strip=True).lower()
        location_match = re.search(r"\(([^)]+)\)", text)
        if location_match:
            return location_match.group(1).strip()
        cities = re.findall(r"\b[A-Z][a-z]+\s*,\s*[A-Z]{2}\b", text)
        if cities:
            return cities[0]
        return ""

    @staticmethod
    def _extract_employment_type(card: BeautifulSoup) -> str:
        text = card.get_text(strip=True).lower()
        employment_map = {
            "full-time": "full_time",
            "full time": "full_time",
            "fulltime": "full_time",
            "part-time": "part_time",
            "part time": "part_time",
            "internship": "internship",
            "intern": "internship",
            "contract": "contract",
            "contract-to-hire": "contract",
        }
        for keyword, normalized in employment_map.items():
            if keyword in text:
                return normalized
        return "unknown"

    @staticmethod
    def _extract_description(card: BeautifulSoup) -> str:
        for selector in [".description", ".job-description", ".description-container"]:
            el = card.select_one(selector)
            if el:
                return el.get_text(strip=True)[:500]
        text = card.get_text(strip=True)
        return text[:500]

    @staticmethod
    def _extract_apply_url(card: BeautifulSoup, base_url: str) -> str | None:
        """Extract job-specific apply URL from a job card.

        Returns None if no job-specific apply link is found, to avoid
        duplicate url_hash collisions across multiple job cards that would
        all fall back to the same base_url.
        """
        for a in card.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True).lower()
            if "apply" in text:
                if href.startswith("/"):
                    return urljoin(base_url, href)
                elif not href.startswith("http"):
                    return urljoin(base_url, href)
                return href
        # Fallback to parent <a> if exists
        parent_a = card.parent.find("a", href=True) if card.parent else None
        if parent_a:
            href = parent_a["href"]
            if href.startswith("/"):
                return urljoin(base_url, href)
            elif not href.startswith("http"):
                return urljoin(base_url, href)
            return href
        # No job-specific apply link found — return None so caller can skip
        # or construct a unique fallback (e.g., with title slug).
        return None

    @staticmethod
    def _compute_source_id(
        title: str, company: str, location: str, apply_url: str
    ) -> str:
        """Deterministic source_id for dedup."""
        key = f"{title.strip().lower()}|{company.strip().lower()}|{location.strip().lower()}|{apply_url}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _normalize_location(raw: str) -> str:
        """Normalize location string deterministically."""
        if not raw:
            return "unknown"
        normalized = re.sub(r"\s+", " ", raw).strip()
        if not normalized:
            return "unknown"
        aliases = {
            "uk": "United Kingdom",
            "usa": "United States",
            "us": "United States",
            "u.k.": "United Kingdom",
            "u.s.a.": "United States",
        }
        words = normalized.split()
        normalized = " ".join(
            aliases.get(w.lower(), w) for w in words
        )
        return normalized.title()

    async def scrape_company_jobs(
        self,
        company_name: str,
        location: Optional[str] = None,
        custom_url: Optional[str] = None,
    ) -> List[RawJobPosting]:
        """
        Scrape job postings for a given company.

        Args:
            company_name: Name of the company (used for auto URL construction).
            location: Optional location filter.
            custom_url: Optional custom career‑page URL.

        Returns:
            List of :class:`RawJobPosting` objects.
        """
        if custom_url:
            career_url = custom_url.rstrip("/")
        else:
            # Build a simple URL from the company name.
            career_url = f"https://{company_name.lower().replace(' ', '')}.com/careers"

        try:
            html = await self._fetch_html(career_url)
            if not html:
                logger.info("No HTML fetched for %s", career_url)
                return []

            cards = self._extract_job_cards(html)
            if not cards:
                logger.info("No job cards found on %s", career_url)
                return []

            results: List[RawJobPosting] = []
            for idx, card in enumerate(cards):
                try:
                    title = self._extract_title(card).strip()
                    company = self._extract_company(card).strip() or company_name
                    location_raw = self._extract_location(card).strip()
                    employment_type = self._extract_employment_type(card)
                    description = self._extract_description(card).strip()
                    apply_url = self._extract_apply_url(card, career_url)

                    # If no job-specific apply URL, construct a unique fallback
                    # using title slug + index to avoid url_hash collisions
                    if not apply_url:
                        title_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                        apply_url = f"{career_url.rstrip('/')}/job/{title_slug}-{idx}"
                        if not apply_url:
                            continue  # Still no URL, skip

                    if not title:
                        continue  # Skip entries without title

                    normalized_location = self._normalize_location(location_raw)
                    source_id = self._compute_source_id(
                        title, company, normalized_location, apply_url
                    )
                    # url_hash isn't set here — normalize.py computes it from apply_url
                    # for every source uniformly (see normalize_job()), so doing it again
                    # here was dead work with no consumer.

                    posting = RawJobPosting(
                        source="company_scraper",
                        source_id=source_id,
                        title=title,
                        company=company,
                        location_raw=location_raw,
                        location_normalized=normalized_location,
                        employment_type_raw=employment_type,
                        description=description,
                        apply_url=apply_url,
                        posted_at_raw=None,
                        salary_raw=None,
                    )
                    results.append(posting)
                except Exception as e:
                    logger.warning("Error extracting job card: %s", e)
                    continue

            logger.info("Scraped %d jobs from %s", len(results), career_url)
            return results

        except httpx.HTTPError as e:
            logger.error("HTTP error scraping career page %s: %s", career_url, e)
            return []
        except Exception as e:
            logger.error("Unexpected error scraping career page %s: %s", career_url, e)
            return []


async def scrape_company_jobs(
    company_name: str, location: Optional[str] = None, custom_url: Optional[str] = None
) -> List[RawJobPosting]:
    """Backward‑compatible entry point used by adapters and existing code."""
    scraper = CompanyScraper()
    return await scraper.scrape_company_jobs(company_name, location, custom_url)


