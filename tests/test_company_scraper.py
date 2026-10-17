"""Tests for the company career-page scraper.

These target the bugs that actually shipped broken: company-field corruption,
a location regex that could never match, casing damage, the dead `location`
argument, and URL probing that only ever tried `/careers`.
"""

import asyncio

import httpx
import pytest

from backend.services.company_scraper import CompanyScraper

# A realistic card shape: labelled company, location, employment type, apply link.
CAREER_HTML = """
<html><body>
  <ul class="job-listings">
    <li class="job-card" data-job-id="1">
      <h3>Senior Backend Engineer</h3>
      <span class="company">Acme Corp</span>
      <div class="location">Berlin, Germany</div>
      <p class="description">Full-time. Build Python services on Kubernetes and Postgres.</p>
      <a href="/jobs/1/apply">Apply now</a>
    </li>
    <li class="job-card" data-job-id="2">
      <h3>Product Designer</h3>
      <span class="company">Acme Corp</span>
      <div class="location">New York, NY</div>
      <p class="description">Full-time. Own end-to-end design for the hiring product surface.</p>
      <a href="/jobs/2/apply">Apply now</a>
    </li>
  </ul>
</body></html>
"""


@pytest.fixture
def scraper():
    return CompanyScraper(max_concurrency=2, jitter_max=0.0)


def _patch_fetch(scraper, pages, calls):
    """Replace _fetch_html with a stub. `pages` maps url -> html or None."""

    async def fake(url):
        calls.append(url)
        return pages.get(url)

    scraper._fetch_html = fake


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------


def test_company_falls_back_to_requested_name_not_card_text(scraper):
    """Regression: the old extractor dumped the entire card text into
    `company`, poisoning every downstream field."""
    cards = CompanyScraper._extract_job_cards(
        '<li class="job-card"><h3>Engineer</h3><p>Lots of description text here '
        "that is definitely not a company name.</p></li>"
    )
    assert cards
    assert CompanyScraper._extract_company(cards[0]) == ""


def test_company_reads_labelled_element(scraper):
    cards = CompanyScraper._extract_job_cards(CAREER_HTML)
    assert CompanyScraper._extract_company(cards[0]) == "Acme Corp"


def test_location_from_labelled_element(scraper):
    cards = CompanyScraper._extract_job_cards(CAREER_HTML)
    assert CompanyScraper._extract_location(cards[0]) == "Berlin, Germany"
    assert CompanyScraper._extract_location(cards[1]) == "New York, NY"


def test_location_city_region_regex_matches_real_casing():
    """Regression: the regex was `[A-Z][a-z]+` run against `.lower()`ed text,
    so it could never match and every location came back empty."""
    html = '<li class="job-card"><h3>Engineer</h3><span>Work from Austin, TX</span></li>'
    card = CompanyScraper._extract_job_cards(html)[0]
    assert CompanyScraper._extract_location(card) == "Austin, TX"


def test_location_rejects_parenthesised_employment_noise():
    """`(Full-time)` must never be captured as the location."""
    html = '<li class="job-card"><h3>Engineer</h3><span>Based in Dublin</span> (Full-time)</li>'
    card = CompanyScraper._extract_job_cards(html)[0]
    assert "full-time" not in CompanyScraper._extract_location(card).lower()


def test_employment_type_is_raw_and_survives_normalization(scraper):
    """Regression: the scraper used to emit an already-mapped "full_time",
    which normalize.parse_employment_type then failed to look up -> "unknown".
    The scraper must emit the raw token; normalize does the mapping."""
    from backend.services.normalize import normalize_job

    cards = CompanyScraper._extract_job_cards(CAREER_HTML)
    raw = CompanyScraper._extract_employment_type(cards[0])
    assert raw == "full-time"

    postings = scraper._build_postings(cards, "Acme", "https://a/c", None)
    assert normalize_job(postings[0]).employment_type == "full_time"


def test_contract_to_hire_maps_to_contract(scraper):
    """Most specific marker must win over the bare "contract" substring."""
    from backend.services.normalize import normalize_job

    html = ('<li class="job-card"><h3>Engineer</h3>'
            '<p>Contract-to-hire, six month engagement with Python work.</p></li>')
    card = CompanyScraper._extract_job_cards(html)[0]
    raw = CompanyScraper._extract_employment_type(card)
    assert raw == "contract-to-hire"
    assert normalize_job(
        scraper._build_postings([card], "Acme", "https://a/c", None)[0]
    ).employment_type == "contract"


def test_apply_url_never_borrows_a_sibling_anchor():
    """Regression: the old parent-<a> fallback could return the *neighbouring*
    card's URL, sending one posting's apply_url to another posting."""
    html = """
    <ul>
      <li class="job-card"><h3>Engineer One</h3>
        <a href="/jobs/one">Details</a></li>
      <li class="job-card"><h3>Engineer Two</h3>
        <a href="/jobs/two">Details</a></li>
    </ul>
    """
    cards = CompanyScraper._extract_job_cards(html)
    urls = [CompanyScraper._extract_apply_url(c, "https://acme.com/careers") for c in cards]
    assert urls == ["https://acme.com/jobs/one", "https://acme.com/jobs/two"]


def test_distinct_cards_get_distinct_urls_and_source_ids(scraper):
    """Two roles with no apply link must not collide on url_hash."""
    html = """
    <ul class="job-listings">
      <li class="job-card"><h3>Data Scientist</h3>
        <div class="location">Remote</div>
        <p>Build forecasting models across the analytics stack.</p></li>
      <li class="job-card"><h3>Data Analyst</h3>
        <div class="location">Remote</div>
        <p>Own reporting dashboards and stakeholder reporting.</p></li>
    </ul>
    """
    postings = scraper._build_postings(
        CompanyScraper._extract_job_cards(html), "Acme", "https://acme.com/careers", None
    )
    assert len(postings) == 2
    assert postings[0].apply_url != postings[1].apply_url
    assert postings[0].source_id != postings[1].source_id


# ---------------------------------------------------------------------------
# Location filter (the previously dead `location` argument)
# ---------------------------------------------------------------------------


def test_location_filter_is_applied(scraper):
    cards = CompanyScraper._extract_job_cards(CAREER_HTML)
    kept = scraper._build_postings(cards, "Acme Corp", "https://acme.com/careers", "berlin")
    assert len(kept) == 1
    assert kept[0].title == "Senior Backend Engineer"


def test_location_filter_is_case_insensitive(scraper):
    cards = CompanyScraper._extract_job_cards(CAREER_HTML)
    assert scraper._build_postings(cards, "Acme", "https://a/c", "NEW YORK, ny")


def test_no_filter_keeps_everything(scraper):
    cards = CompanyScraper._extract_job_cards(CAREER_HTML)
    assert len(scraper._build_postings(cards, "Acme", "https://a/c", None)) == 2


# ---------------------------------------------------------------------------
# Career URL discovery (previously only /careers was ever tried)
# ---------------------------------------------------------------------------


def test_candidate_urls_are_deduped_and_ordered(scraper):
    urls = scraper._career_url_candidates("https://acme.com")
    assert urls[0] == "https://acme.com/careers"
    assert len(urls) == len(set(urls))
    assert all(u.startswith("https://acme.com") for u in urls)


def test_base_site_from_company_name():
    assert CompanyScraper._base_site("Acme Corp") == "https://acmecorp.com"
    assert CompanyScraper._base_site("Foo-Bar, Inc.") == "https://foobarinc.com"


def test_explicit_custom_url_skips_probing():
    calls = []
    s = CompanyScraper()

    async def fake(url):
        calls.append(url)
        return CAREER_HTML

    s._fetch_html = fake
    out = asyncio.run(s.scrape_company_jobs("Acme", custom_url="https://boards.greenhouse.io/acme"))
    assert calls == ["https://boards.greenhouse.io/acme"]
    assert len(out) == 2


def test_probing_falls_through_to_a_later_pattern():
    """The point of discovery: /careers 404s, /jobs works."""
    calls = []

    async def fake(url):
        calls.append(url)
        if url.endswith("/jobs"):
            return CAREER_HTML
        return None

    s = CompanyScraper()
    s._fetch_html = fake
    out = asyncio.run(s.scrape_company_jobs("Acme"))
    assert len(out) == 2
    assert all(p.apply_url.startswith("https://acme.com/jobs/") for p in out)
    assert "https://acme.com/careers" in calls


def test_probing_skips_a_page_with_no_job_listings():
    """A generic landing page mentions 'career' but lists nothing — keep probing."""
    calls = []

    async def fake(url):
        calls.append(url)
        if url.endswith("/jobs"):
            return CAREER_HTML
        return "<html><body><h1>About our career story</h1></body></html>"

    s = CompanyScraper()
    s._fetch_html = fake
    out = asyncio.run(s.scrape_company_jobs("Acme"))
    assert len(out) == 2


def test_network_failure_returns_empty_not_exception():
    async def boom(url):
        raise httpx.ConnectError("nope")

    s = CompanyScraper()
    s._fetch_html = boom
    assert asyncio.run(s.scrape_company_jobs("Acme")) == []


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_roundtrip_and_ttl_expiry(scraper):
    scraper._cache_put("https://x/careers", "<html/>")
    assert scraper._cache_get("https://x/careers") == "<html/>"

    scraper._cache_ttl = 0.0001
    import time as _t

    _t.sleep(0.01)
    assert scraper._cache_get("https://x/careers") is None


def test_cache_evicts_fifo_at_capacity(scraper):
    scraper._cache_size = 2
    for i in range(3):
        scraper._cache_put(f"https://x/{i}", "<html/>")
    assert len(scraper._html_cache) == 2
    assert scraper._cache_get("https://x/0") is None


def test_fetch_uses_cache_and_does_not_refetch(scraper):
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=CAREER_HTML)

    async def run():
        transport = httpx.MockTransport(handler)
        original = scraper._get_client

        def get_client():
            c = original()
            c._transport = transport
            return c

        scraper._get_client = get_client
        try:
            a = await scraper._fetch_html("https://acme.com/careers")
            b = await scraper._fetch_html("https://acme.com/careers")
        finally:
            await scraper.aclose()
        return a, b

    a, b = asyncio.run(run())
    assert a == b == CAREER_HTML
    assert len(calls) == 1


def test_retry_happens_on_503_then_succeeds(scraper):
    scraper._max_retries = 2
    attempts = []

    async def handler(request):
        attempts.append(str(request.url))
        if len(attempts) < 2:
            return httpx.Response(503)
        return httpx.Response(200, text="<html>ok</html>")

    async def run():
        original = scraper._get_client

        def get_client():
            c = original()
            c._transport = httpx.MockTransport(handler)
            return c

        scraper._get_client = get_client
        try:
            return await scraper._fetch_html("https://acme.com/careers")
        finally:
            await scraper.aclose()

    assert asyncio.run(run()) == "<html>ok</html>"
    assert len(attempts) == 2


def test_404_is_not_retried(scraper):
    """Fail fast: a 404 will not become a 200 on retry."""
    attempts = []

    async def handler(request):
        attempts.append(str(request.url))
        return httpx.Response(404)

    async def run():
        original = scraper._get_client

        def get_client():
            c = original()
            c._transport = httpx.MockTransport(handler)
            return c

        scraper._get_client = get_client
        try:
            return await scraper._fetch_html("https://acme.com/careers")
        finally:
            await scraper.aclose()

    assert asyncio.run(run()) is None
    assert len(attempts) == 1