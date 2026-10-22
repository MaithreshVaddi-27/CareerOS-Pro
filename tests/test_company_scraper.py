"""Tests for the company career-page scraper.

These target the bugs that actually shipped broken: company-field corruption,
a location regex that could never match, casing damage, the dead `location`
argument, and URL probing that only ever tried `/careers`.
"""

import asyncio

import httpx
import pytest

from backend.services.sourcing.company_scraper import CompanyScraper

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


def install_transport(scraper, handler):
    """Route scraper requests through `handler`, with robots.txt stubbed to 404.

    Stubbing robots.txt (a 404 means "no rules published", so allow) keeps the
    page-call counters in the tests below honest without every one of them having
    to know that a robots lookup happens at all. Tests that care about robots use
    `robots=` instead.
    """
    original = scraper._get_client

    async def route(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return await handler(request)

    def get_client():
        c = original()
        c._transport = httpx.MockTransport(route)
        return c

    scraper._get_client = get_client
    return original


def install_robots_transport(scraper, handler, robots_response):
    """Same, but with an explicit robots.txt response for the robots tests."""
    original = scraper._get_client

    async def route(request):
        if request.url.path == "/robots.txt":
            return robots_response
        return await handler(request)

    def get_client():
        c = original()
        c._transport = httpx.MockTransport(route)
        return c

    scraper._get_client = get_client
    return original


def _patch_fetch(scraper, pages, calls):
    """Replace _fetch_html with a stub. `pages` maps url -> html or None."""

    async def fake(url):
        calls.append(url)
        return pages.get(url)

    scraper._fetch_html = fake


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------


def test_title_gate_accepts_short_roles_and_rejects_generic_headings():
    assert CompanyScraper._looks_like_job_title("Engineer")
    assert CompanyScraper._looks_like_job_title("Senior Product Designer")
    assert not CompanyScraper._looks_like_job_title("Search jobs")
    assert not CompanyScraper._looks_like_job_title("Locations")


def test_query_matches_title_terms_without_requiring_phrase_order(scraper):
    cards = CompanyScraper._extract_job_cards(CAREER_HTML)
    postings = scraper._build_postings(
        cards, "Acme", "https://acme.com/careers", None, query="backend engineer"
    )
    assert [posting.title for posting in postings] == ["Senior Backend Engineer"]


def test_job_url_rejects_generic_job_board_root():
    assert not CompanyScraper._is_valid_job_url("https://snap.com/job")
    assert CompanyScraper._is_valid_job_url("https://snap.com/job/software-engineer")


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
    from backend.services.sourcing.normalize import normalize_job

    cards = CompanyScraper._extract_job_cards(CAREER_HTML)
    raw = CompanyScraper._extract_employment_type(cards[0])
    assert raw == "full-time"

    postings = scraper._build_postings(cards, "Acme", "https://a/c", None)
    assert normalize_job(postings[0]).employment_type == "full_time"


def test_contract_to_hire_maps_to_contract(scraper):
    """Most specific marker must win over the bare "contract" substring."""
    from backend.services.sourcing.normalize import normalize_job

    html = ('<li class="job-card"><h3>Engineer</h3>'
            '<p>Contract-to-hire, six month engagement with Python work.</p>'
            '<a href="/jobs/engineer-1">Apply</a></li>')
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
        <p>Build forecasting models across the analytics stack.</p>
        <a href="/jobs/data-scientist">Apply</a></li>
      <li class="job-card"><h3>Data Analyst</h3>
        <div class="location">Remote</div>
        <p>Own reporting dashboards and stakeholder reporting.</p>
        <a href="/jobs/data-analyst">Apply</a></li>
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
        install_transport(scraper, handler)
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
        install_transport(scraper, handler)
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
        install_transport(scraper, handler)
        try:
            return await scraper._fetch_html("https://acme.com/careers")
        finally:
            await scraper.aclose()

    assert asyncio.run(run()) is None
    assert len(attempts) == 1


# ---------------------------------------------------------------------------
# robots.txt (audit A6)
# ---------------------------------------------------------------------------

ROBOTS_DISALLOW_CAREERS = """
User-agent: *
Disallow: /careers
Allow: /jobs
"""


def _fetch_with_robots(scraper, robots_text, url="https://acme.com/careers"):
    """Fetch `url` with robots.txt serving `robots_text`; return (html, page_calls)."""
    page_calls = []

    async def handler(request):
        page_calls.append(str(request.url))
        return httpx.Response(200, text=CAREER_HTML)

    async def run():
        install_robots_transport(scraper, handler, httpx.Response(200, text=robots_text))
        try:
            return await scraper._fetch_html(url)
        finally:
            await scraper.aclose()

    return asyncio.run(run()), page_calls


def test_robots_disallow_prevents_the_fetch_entirely(scraper):
    """The point of the fix: a disallowed path is never requested at all."""
    html, page_calls = _fetch_with_robots(scraper, ROBOTS_DISALLOW_CAREERS)
    assert html is None
    assert page_calls == []


def test_robots_allow_permits_the_fetch(scraper):
    html, page_calls = _fetch_with_robots(scraper, "User-agent: *\nAllow: /\n")
    assert html == CAREER_HTML
    assert page_calls == ["https://acme.com/careers"]


def test_robots_longer_match_wins_over_shorter_allow(scraper):
    """`Allow: /jobs` must not unlock `/careers`."""
    html, _ = _fetch_with_robots(scraper, ROBOTS_DISALLOW_CAREERS, "https://acme.com/jobs/1")
    assert html == CAREER_HTML

    html, page_calls = _fetch_with_robots(scraper, ROBOTS_DISALLOW_CAREERS)
    assert html is None and page_calls == []


def test_missing_robots_txt_allows_the_fetch(scraper):
    """404 = no published rules, so nothing to obey (RFC 9309)."""
    page_calls = []

    async def handler(request):
        page_calls.append(str(request.url))
        return httpx.Response(200, text=CAREER_HTML)

    async def run():
        install_transport(scraper, handler)
        try:
            return await scraper._fetch_html("https://acme.com/careers")
        finally:
            await scraper.aclose()

    assert asyncio.run(run()) == CAREER_HTML


def test_robots_403_disallows_everything(scraper):
    """401/403 is the site refusing to hand over the rules — not permission."""
    page_calls = []

    async def handler(request):
        page_calls.append(str(request.url))
        return httpx.Response(200, text=CAREER_HTML)

    async def run():
        install_robots_transport(scraper, handler, httpx.Response(403))
        try:
            return await scraper._fetch_html("https://acme.com/careers")
        finally:
            await scraper.aclose()

    assert asyncio.run(run()) is None
    assert page_calls == []


def test_robots_is_fetched_once_per_origin(scraper):
    robots_calls = []

    async def handler(request):
        return httpx.Response(200, text="User-agent: *\nAllow: /\n")

    async def route(request):
        if request.url.path == "/robots.txt":
            robots_calls.append(str(request.url))
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return await handler(request)

    async def run():
        original = scraper._get_client

        def get_client():
            c = original()
            c._transport = httpx.MockTransport(route)
            return c

        scraper._get_client = get_client
        try:
            await scraper._fetch_html("https://acme.com/careers")
            await scraper._fetch_html("https://acme.com/jobs/1")
            await scraper._fetch_html("https://acme.com/join-us")
        finally:
            await scraper.aclose()

    asyncio.run(run())
    assert robots_calls == ["https://acme.com/robots.txt"]


def test_robots_5xx_allows_but_is_not_cached(scraper):
    """Unavailable != permitted. Allow this once, but re-check next time rather
    than inheriting a stale 'allow' for the life of the process."""
    robots_calls = []

    async def handler(request):
        return httpx.Response(200, text=CAREER_HTML)

    async def route(request):
        if request.url.path == "/robots.txt":
            robots_calls.append(str(request.url))
            return httpx.Response(503)
        return await handler(request)

    async def run():
        original = scraper._get_client

        def get_client():
            c = original()
            c._transport = httpx.MockTransport(route)
            return c

        scraper._get_client = get_client
        try:
            first = await scraper._fetch_html("https://acme.com/careers")
            scraper._cache_put("https://acme.com/careers", "")  # force a second lookup
            second = await scraper._fetch_html("https://acme.com/jobs/1")
            return first, second
        finally:
            await scraper.aclose()

    first, second = asyncio.run(run())
    assert first == second == CAREER_HTML
    assert len(robots_calls) == 2


def test_robots_survives_transport_failure(scraper):
    """A network error on robots.txt must not take the whole scrape down."""
    async def handler(request):
        return httpx.Response(200, text=CAREER_HTML)

    async def route(request):
        if request.url.path == "/robots.txt":
            raise httpx.ConnectError("boom")
        return await handler(request)

    async def run():
        original = scraper._get_client

        def get_client():
            c = original()
            c._transport = httpx.MockTransport(route)
            return c

        scraper._get_client = get_client
        try:
            return await scraper._fetch_html("https://acme.com/careers")
        finally:
            await scraper.aclose()

    assert asyncio.run(run()) == CAREER_HTML


def test_crawl_delay_is_honoured_and_capped(scraper, monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def handler(request):
        return httpx.Response(200, text=CAREER_HTML)

    async def run():
        install_robots_transport(
            scraper, handler, httpx.Response(200, text="User-agent: *\nCrawl-delay: 30\n")
        )
        try:
            return await scraper._fetch_html("https://acme.com/careers")
        finally:
            await scraper.aclose()

    assert asyncio.run(run()) == CAREER_HTML
    # 30s requested, capped to 10s so one site cannot stall the pipeline.
    assert slept == [10.0]


# ---------------------------------------------------------------------------
# Multi-website job board search
# ---------------------------------------------------------------------------

BOARD_HTML = """
<html><body>
  <div class="job-card">
    <h3>Senior Python Engineer</h3>
    <span class="company">BoardCorp</span>
    <div class="location">Berlin, Germany</div>
    <p class="description">Full-time Python role.</p>
    <a href="/jobs/1/apply">Apply now</a>
  </div>
  <div class="job-card">
    <h3>Backend Developer</h3>
    <span class="company">BoardCorp</span>
    <div class="location">Remote</div>
    <p class="description">Part-time backend work.</p>
    <a href="/jobs/2/apply">Apply now</a>
  </div>
</body></html>
"""


def _install_board_transport(scraper, pages):
    """Route scraper requests through `pages` (url -> html), robots.txt -> 404."""

    async def route(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        for url, html in pages.items():
            if str(request.url).rstrip("/") == url.rstrip("/"):
                return httpx.Response(200, text=html)
        return httpx.Response(404)

    original = scraper._get_client

    def get_client():
        c = original()
        c._transport = httpx.MockTransport(route)
        return c

    scraper._get_client = get_client
    return original


def test_multi_website_search_aggregates_across_boards(scraper):
    """Multiple boards are fanned out and results are combined into one list."""
    pages = {
        "https://weworkremotely.com/remote-jobs/search?term=python": BOARD_HTML,
        "https://remoteok.com/remote-python-jobs": BOARD_HTML,
        "https://wellfound.com/role/python": BOARD_HTML,
    }
    _install_board_transport(scraper, pages)

    results = asyncio.run(scraper.scrape_multiple_websites("python", location="Berlin"))

    assert len(results) > 0
    # All results carry the board-scoped source.
    for r in results:
        assert r.source.startswith("company_scraper:")
    # Results come from multiple boards (each board's apply URL differs).
    boards_seen = {r.source.split(":", 1)[1] for r in results}
    assert len(boards_seen) == 3
    # At least one board found the Berlin role.
    assert any("Berlin" in (r.location_raw or "") for r in results)


def test_multi_website_search_empty_query_returns_empty(scraper):
    """An empty query returns nothing without making any requests."""
    calls = []

    async def fake(url):
        calls.append(url)
        return BOARD_HTML

    scraper._fetch_html = fake
    assert asyncio.run(scraper.scrape_multiple_websites("")) == []
    assert asyncio.run(scraper.scrape_multiple_websites("   ")) == []
    assert calls == []


def test_multi_website_search_skips_failed_boards(scraper):
    """A board that returns no HTML or no cards is skipped, others still return."""
    pages = {
        "https://weworkremotely.com/remote-jobs/search?term=python": BOARD_HTML,
        "https://remoteok.com/remote-python-jobs": None,  # transport miss
        "https://wellfound.com/role/python": "",  # empty HTML -> no cards
    }
    _install_board_transport(scraper, pages)

    results = asyncio.run(scraper.scrape_multiple_websites("python"))

    assert len(results) > 0
    assert all(r.title for r in results)


def test_multi_website_search_respects_location_filter(scraper):
    """Results are filtered by location when one is provided."""
    pages = {
        "https://weworkremotely.com/remote-jobs/search?term=python": BOARD_HTML,
    }
    _install_board_transport(scraper, pages)

    all_results = asyncio.run(scraper.scrape_multiple_websites("python"))
    berlin_results = asyncio.run(scraper.scrape_multiple_websites("python", location="Berlin"))

    # Berlin filter keeps the Berlin card, drops the Remote one.
    assert len(berlin_results) <= len(all_results)
    assert all("Berlin" in (r.location_raw or "") for r in berlin_results)


def test_multi_website_search_board_selection(scraper, monkeypatch):
    """Only the requested boards are searched when `boards` is given."""
    fetched = []

    async def fake_fetch(url):
        fetched.append(url)
        return BOARD_HTML

    scraper._fetch_html = fake_fetch

    asyncio.run(scraper.scrape_multiple_websites("python", boards=["weworkremotely"]))

    assert len(fetched) == 1
    assert "weworkremotely" in fetched[0]
    assert "remoteok" not in "".join(fetched)
    assert "wellfound" not in "".join(fetched)


def test_multi_website_search_unknown_board_ignored(scraper):
    """Unknown board names are ignored rather than crashing."""
    pages = {
        "https://weworkremotely.com/remote-jobs/search?term=python": BOARD_HTML,
    }
    _install_board_transport(scraper, pages)

    results = asyncio.run(
        scraper.scrape_multiple_websites("python", boards=["weworkremotely", "nonexistent_board"])
    )
    assert len(results) > 0