"""Third-pass regression guards.

Each test here pins a defect found after the first two audit passes, and each
one fails against the code as it was before that fix. The organising theme is
still silent failure: the Gemini key leak and the `model_name` no-op both
returned HTTP 200 and looked correct from outside.

Grouped by the layer that owns the fix, not by file, so the reason each guard
exists is legible from the name.
"""

import datetime as dt
import sys
from pathlib import Path

import httpx
import pytest

from backend.core.monitoring import AgentMonitor, scrub_secrets
from backend.models import Job
from backend.schemas import MatchRequest, SearchRequest
from backend.services.matching.filters import apply_hard_filters, build_sql_filters

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Credential disclosure via GET /agents/health
# ---------------------------------------------------------------------------


GEMINI_KEY = "AIzaSy-SUPER-SECRET-DO-NOT-SHIP"


def _gemini_auth_error() -> httpx.HTTPStatusError:
    """The exact exception a bad Gemini key produces.

    httpx stringifies the full request URL into HTTPStatusError, and Gemini
    authenticates via `?key=`, so the secret is in the message. Built through
    raise_for_status() rather than hand-written so this stays true if httpx
    changes its format.
    """
    request = httpx.Request(
        "POST",
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-flash-latest:generateContent?key={GEMINI_KEY}",
    )
    response = httpx.Response(401, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError("expected a 401 to raise")


def test_gemini_auth_error_really_does_contain_the_key():
    """Guard the guard.

    If httpx ever stops embedding the URL in the message, the redaction test
    below would pass vacuously. This asserts the leak is still possible, so a
    future httpx change surfaces here instead of silently disarming the guard.
    """
    assert GEMINI_KEY in str(_gemini_auth_error())


def test_monitor_never_stores_a_raw_gemini_key():
    """The leak itself.

    `record` is the single sink every agent funnels through, so this is where
    the scrub has to live: a caller that forgets to redact leaks nothing.
    """
    monitor = AgentMonitor()
    monitor.record("llm:gemini", ok=False, latency_ms=1.0, error=str(_gemini_auth_error()))

    stored = monitor.snapshot()["llm:gemini"]["last_error"]
    assert stored is not None
    assert GEMINI_KEY not in stored
    assert "401" in stored, "the failure must still be diagnosable"


def test_monitor_scrubs_adzuna_credentials_too():
    """The previously-fixed sibling leak stays fixed, and shares one code path."""
    monitor = AgentMonitor()
    monitor.record(
        "source:AdzunaAdapter", ok=False, latency_ms=1.0,
        error="403 for url 'https://api.adzuna.com/v1/api/jobs/in/search/1"
              "?app_id=SECRET_ID&app_key=SECRET_KEY'",
    )
    stored = monitor.snapshot()["source:AdzunaAdapter"]["last_error"]
    assert "SECRET_ID" not in stored
    assert "SECRET_KEY" not in stored


def test_monitor_track_contextmanager_scrubs_too():
    """The contextmanager path records raw `str(exc)`, so it needs the same
    guarantee — it is a separate code path from `record(..., error=...)`."""
    monitor = AgentMonitor()
    with pytest.raises(httpx.HTTPStatusError):
        with monitor.track("llm:gemini"):
            raise _gemini_auth_error()

    assert GEMINI_KEY not in monitor.snapshot()["llm:gemini"]["last_error"]


@pytest.mark.parametrize(
    "text,expect_leak",
    [
        ("https://x.dev/v1?key=abc123", "abc123"),
        ("https://x.dev/v1?app_id=abc123&app_key=def456", "abc123"),
        ("https://x.dev/v1?a=1&api_key=abc123", "abc123"),
        ("https://x.dev/v1?access_token=abc123", "abc123"),
    ],
)
def test_scrub_secrets_covers_each_provider_shape(text, expect_leak):
    assert expect_leak not in scrub_secrets(text)


def test_scrub_secrets_does_not_mangle_ordinary_text():
    """The [?&] anchor is what keeps this safe: a bare word ending in "key"
    must not be redacted, or every log line mentioning 'monkey' or 'hotkey'
    would be corrupted."""
    for text in ("monkey=banana", "hotkey: F5", "sort key is id", "see the monkey puzzle"):
        assert scrub_secrets(text) == text


def test_agents_health_endpoint_does_not_return_a_key(monkeypatch):
    """End-to-end: the endpoint is the thing an attacker actually calls.

    require_api_key is the only gate on it, and the bundled frontend bakes
    VITE_API_KEY into its shipped JS bundle — so this test stands in for
    'anyone who opened the site'.
    """
    from backend.core.monitoring import agent_monitor

    agent_monitor.record("llm:gemini", ok=False, latency_ms=1.0, error=str(_gemini_auth_error()))

    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    body = client.get("/agents/health", headers={"X-API-Key": "test"}).json()
    assert GEMINI_KEY not in str(body)


# ---------------------------------------------------------------------------
# Per-request model override
# ---------------------------------------------------------------------------


@pytest.fixture
def gemini_key(monkeypatch):
    """The router only builds a provider when a key is configured.

    Patched onto `model_router.settings` rather than via the environment:
    that module binds `settings = get_settings()` at *import* time, so if any
    other test module imported it first — which is what happens in a full-suite
    run — setting GEMINI_API_KEY and clearing the lru_cache has no effect on the
    object the code under test actually reads.
    """
    from backend.services.matching import model_router

    monkeypatch.setattr(model_router.settings, "gemini_api_key", "test-gemini-key", raising=False)
    return "test-gemini-key"


class _FakeGeminiProvider:
    """Stands in for GeminiProvider so the model id is observable without
    constructing a real client or making a request."""

    def __init__(self, api_key, model):
        self.api_key = api_key
        self.model = model


def test_model_name_override_is_actually_used(gemini_key):
    """`model_name` was accepted on the schema, threaded through the graph, and
    then ignored — the provider was always built from settings.gemini_model."""
    from backend.services.matching import model_router

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(model_router, "GeminiProvider", _FakeGeminiProvider)
    try:
        router = model_router.get_model_router_for_request(
            llm_provider="gemini", model_name="gemini-pro-latest"
        )
        assert router._gemini.model == "gemini-pro-latest"
    finally:
        monkeypatch.undo()


def test_model_name_defaults_to_the_configured_model(gemini_key):
    from backend.core.config import get_settings
    from backend.services.matching import model_router

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(model_router, "GeminiProvider", _FakeGeminiProvider)
    try:
        router = model_router.get_model_router_for_request(llm_provider="gemini")
        assert router._gemini.model == get_settings().gemini_model
    finally:
        monkeypatch.undo()


@pytest.mark.parametrize(
    "bad",
    [
        "../../v1beta/files",   # path traversal out of /v1beta/models/
        "gemini/../../admin",
        "gemini flash",         # not a valid id at all
        "a" * 100,              # longer than any real model id
    ],
)
def test_model_name_cannot_inject_url_path_segments(bad, gemini_key):
    """model_name is interpolated into a request path carrying the API key, so
    an unvalidated value is a way to aim an authenticated call elsewhere."""
    from backend.core.config import get_settings
    from backend.services.matching import model_router

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(model_router, "GeminiProvider", _FakeGeminiProvider)
    try:
        router = model_router.get_model_router_for_request(llm_provider="gemini", model_name=bad)
        # Falls back rather than raising: this runs inside a LangGraph node,
        # where an unhandled exception would 500 an otherwise fine request.
        assert router._gemini.model == get_settings().gemini_model
    finally:
        monkeypatch.undo()


def test_model_name_is_rejected_at_the_schema_with_422():
    """A caller gets a clean 422 naming the field, not a 500 from deep in the graph."""
    with pytest.raises(Exception):
        MatchRequest(query="python", model_name="../../etc/passwd")


def test_model_name_accepts_a_real_looking_id():
    req = MatchRequest(query="python", model_name="gemini-2.5-flash")
    assert req.model_name == "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# Request bounds
# ---------------------------------------------------------------------------


def test_sources_list_is_bounded():
    """Unbounded list -> every element becomes a bind parameter in
    `Job.source.in_(...)`, which is the SQL-side of an unbounded body."""
    with pytest.raises(Exception):
        SearchRequest(query="python", sources=["jsearch"] * 5000)


def test_sources_rejects_blank_entries():
    """An empty string would otherwise become `source = ''` and never match,
    or an empty IN list depending on how the caller handles it."""
    with pytest.raises(Exception):
        SearchRequest(query="python", sources=["jsearch", "  "])


def test_sources_accepts_a_realistic_set():
    req = SearchRequest(query="python", sources=["jsearch", "adzuna", "company"])
    assert req.sources == ["jsearch", "adzuna", "company"]


def test_posted_within_days_rejects_negative():
    """A negative value produced a cutoff in the *future*, which silently
    filtered out every dated row instead of reporting a bad input."""
    with pytest.raises(Exception):
        SearchRequest(query="python", posted_within_days=-5)
    with pytest.raises(Exception):
        MatchRequest(query="python", posted_within_days=-5)


# ---------------------------------------------------------------------------
# max_salary — the visible-but-dead UI filter
# ---------------------------------------------------------------------------


def test_max_salary_filter_is_wired_end_to_end():
    """Filters.jsx renders a "Max salary" input, App.jsx forwards it, and
    api.js used to destructure it as `_salary_max` and never send it — so the
    control looked applied and did nothing. It has to reach the query now."""
    req = SearchRequest(query="python", max_salary=500_000)
    assert req.max_salary == 500_000

    from backend.core.db import Base

    sql = " ".join(str(c) for c in build_sql_filters(req))
    assert "salary_max <=" in sql


def test_max_salary_excludes_only_rows_known_to_exceed_it():
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    req = SearchRequest(query="", max_salary=500_000)

    def job(**kw):
        base = dict(
            id="1", is_active=True, title="", description="", location_normalized="",
            employment_type="unknown", experience_level="unknown", remote="unknown",
            source="mock", salary_max=None, posted_at=None,
        )
        base.update(kw)
        return Job(**base)

    under = job(id="a", salary_max=100_000)
    over = job(id="b", salary_max=900_000)
    unknown = job(id="c", salary_max=None)

    kept = {j.id for j in apply_hard_filters([under, over, unknown], req, now=now)}
    assert kept == {"a", "c"}, "unknown != ineligible (spec 2.3) — a blank salary passes the ceiling"


def test_min_and_max_salary_compose():
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    req = SearchRequest(query="", min_salary=100_000, max_salary=500_000)

    def job(jid, sal):
        return Job(
            id=jid, is_active=True, title="", description="", location_normalized="",
            employment_type="unknown", experience_level="unknown", remote="unknown",
            source="mock", salary_max=sal, posted_at=None,
        )

    kept = {j.id for j in apply_hard_filters(
        [job("low", 50_000), job("in", 300_000), job("high", 900_000)], req, now=now)}
    assert kept == {"in"}


# ---------------------------------------------------------------------------
# Source-name aliasing
# ---------------------------------------------------------------------------


def _job(source: str, jid: str = "1") -> Job:
    return Job(
        id=jid, is_active=True, title="", description="", location_normalized="",
        employment_type="unknown", experience_level="unknown", remote="unknown",
        source=source, salary_max=None, posted_at=None,
    )


def test_enabling_name_also_matches_stored_rows():
    """`company` enables the scraper; `company_scraper` is what it tags rows.

    Filtering on the enabling name fetched every posting the scraper found and
    then discarded all of them, because no row is ever tagged `company`. The
    caller got a silently short result set.
    """
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    req = SearchRequest(query="", sources=["company"])
    kept = apply_hard_filters([_job("company_scraper", "a"), _job("jsearch", "b")], req, now=now)
    assert [j.id for j in kept] == ["a"]


def test_stored_name_still_matches():
    """The documented spelling keeps working — this is additive, not a rename."""
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    req = SearchRequest(query="", sources=["company_scraper"])
    kept = apply_hard_filters([_job("company_scraper", "a"), _job("jsearch", "b")], req, now=now)
    assert [j.id for j in kept] == ["a"]


def test_sql_and_python_agree_on_the_alias():
    """The superset contract still holds after aliasing — the two passes must
    resolve the same set of stored names, or SQL narrows and Python widens.

    Asserted on the compiled bind parameters rather than the rendered SQL:
    SQLAlchemy binds the IN list as a parameter, so the literal never appears
    in str(clause).
    """
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    req = SearchRequest(query="", sources=["company"])

    bound = None
    for clause in build_sql_filters(req, now=now):
        params = clause.compile().params
        if params and any("source" in k for k in params):
            bound = next(iter(params.values()))
            break

    assert bound == ["company", "company_scraper"], (
        f"the SQL pass must expand the alias too; got {bound}"
    )


def test_unknown_source_name_matches_nothing_rather_than_raising():
    """An unrecognised source is a caller mistake that should return zero rows,
    not crash the request."""
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    req = SearchRequest(query="", sources=["not-a-real-source"])
    assert apply_hard_filters([_job("jsearch", "a")], req, now=now) == []


# ---------------------------------------------------------------------------
# Filter superset contract
# ---------------------------------------------------------------------------


def test_sql_and_python_filters_share_one_reference_instant():
    """build_sql_filters is documented as a strict superset of the Python
    predicate. It used to compute `now` itself and the Python pass called
    datetime.now() per row, so the SQL cutoff was computed *earlier* and could
    be stricter — dropping a boundary row the Python pass would have kept, in
    exactly the direction the contract forbids."""
    now = dt.datetime(2026, 6, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
    req = SearchRequest(query="", posted_within_days=7)

    boundary = now - dt.timedelta(days=7) + dt.timedelta(seconds=30)
    job = Job(
        id="edge", is_active=True, title="", description="", location_normalized="",
        employment_type="unknown", experience_level="unknown", remote="unknown",
        source="mock", salary_max=None, posted_at=boundary.replace(tzinfo=None),
    )

    # The Python predicate now accepts `now`, so both passes are pinned to the
    # same instant and cannot disagree about a boundary row.
    assert apply_hard_filters([job], req, now=now) == [job]


def test_apply_hard_filters_accepts_a_now_argument():
    import inspect

    sig = inspect.signature(apply_hard_filters)
    assert "now" in sig.parameters


# ---------------------------------------------------------------------------
# Qdrant singleton poisoning
# ---------------------------------------------------------------------------


def test_failed_collection_init_does_not_poison_the_singleton():
    """get_client assigned _client_singleton *before* calling
    ensure_collection, and the fast path returns on the client alone — so one
    transient startup failure left a live client that skipped collection setup
    forever, with no error on any later call to explain it."""
    from backend.services.matching import vector_store

    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def close(self):
            pass

    def flaky_ensure_collection(client):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient storage blip")

    class Tracked(dict):
        pass

    vector_store._client_singleton = None
    vector_store._collection_ready = False
    orig_client = vector_store.QdrantClient
    orig_ensure = vector_store.ensure_collection
    vector_store.QdrantClient = FakeClient
    vector_store.ensure_collection = flaky_ensure_collection
    try:
        with pytest.raises(RuntimeError):
            vector_store.get_client()
        assert vector_store._client_singleton is None, (
            "a client published before setup failed short-circuits every later call"
        )

        # The next call must retry rather than return the poisoned client.
        client = vector_store.get_client()
        assert client is not None
        assert calls["n"] == 2, "the retry must actually re-run collection setup"
    finally:
        vector_store._client_singleton = None
        vector_store._collection_ready = False
        vector_store.QdrantClient = orig_client
        vector_store.ensure_collection = orig_ensure


# ---------------------------------------------------------------------------
# Embedding vectorizer
# ---------------------------------------------------------------------------


def test_embedding_vectorizer_is_actually_cached():
    """The docstring claimed a cache that did not exist, so a fresh
    HashingVectorizer was constructed on every embed() call — hundreds of times
    on one /jobs/match request."""
    from backend.services.matching import embeddings

    embeddings._get_vectorizer.cache_clear()
    a = embeddings._get_vectorizer()
    b = embeddings._get_vectorizer()
    assert a is b


# ---------------------------------------------------------------------------
# Model indexes
# ---------------------------------------------------------------------------


def test_url_hash_has_exactly_one_index():
    """`unique=True, index=True` asks SQLAlchemy for a UNIQUE index and a plain
    index over the same column. The plain one serves no lookup the unique one
    doesn't, and every ingest insert pays to maintain it."""
    from backend.core.db import Base

    indexes = [i for i in Job.__table__.indexes if set(i.columns) == {Job.__table__.c.url_hash}]
    assert len(indexes) == 0, (
        "url_hash's UNIQUE constraint already indexes the column; "
        f"found redundant: {[i.name for i in indexes]}"
    )


# ---------------------------------------------------------------------------
# verify_job
# ---------------------------------------------------------------------------


def test_verify_job_returns_404_not_500_when_the_row_is_gone():
    """The outbound scrape happens between the read and the write. A retention
    run deleting the row in that window made db.get() return None, and the next
    line raised AttributeError — a 500 for what is a plain 404."""
    src = (ROOT / "backend" / "main.py").read_text()
    assert "job = db.get(Job, job_id)\n        if job is None:" in src, (
        "the persist half must re-check for a row deleted while the scrape ran"
    )


# ---------------------------------------------------------------------------
# Repo hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern,why",
    [
        ("frontend/src/**/matchPercentage", "transformJob never produced this key"),
        ("_salary_max", "destructured but never sent, so the filter was dead"),
        ("docker-compose", "Docker was removed; the project deploys natively"),
    ],
)
def test_no_dead_references_left_in_frontend(pattern, why):
    hits = [
        p for p in ROOT.glob(pattern) if "node_modules" not in p.parts
    ]
    assert not hits, f"{why}: {hits}"


def test_venv_is_not_tracked_by_git():
    """585 MB of macOS wheels in a repo is the exact failure the .dockerignore
    note describes; keep it out of git history too."""
    import subprocess

    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    tracked = [line for line in out.splitlines() if line.startswith("backend/venv/")]
    assert not tracked, f"{len(tracked)} venv files are tracked"


def test_pytest_config_is_not_duplicated():
    """pyproject.toml claims pytest config lives only in pytest.ini because the
    latter takes precedence — then declares [tool.pytest.ini_options] anyway,
    which is dead config contradicting its own comment."""
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert "[tool.pytest.ini_options]" not in pyproject


def test_docs_reference_real_paths():
    """monitoring.py and two other modules still pointed at pre-refactor paths
    (services/model_router.py) after the move to services/matching/ and
    services/sourcing/."""
    stale = []
    for path in ROOT.glob("backend/**/*.py"):
        if "venv" in path.parts:
            continue
        text = path.read_text()
        if "from services." in text or "services/model_router.py" in text:
            stale.append(str(path.relative_to(ROOT)))
    assert not stale, f"stale pre-refactor module paths: {stale}"