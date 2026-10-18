"""Route handlers in `backend/main.py`.

These cover what audit item 9 asked for: the route handlers themselves, their
error shapes, and `/health` assembly.

Two things had to be worked around to get here, both worth knowing:

* `/jobs/*` take their session via `Depends(get_db)`, so
  `app.dependency_overrides` reaches them.
* `/health` and `/health/ready` call `get_db()` **directly** rather than through
  `Depends`, so the override does not reach them — those two tests patch
  `backend.main.get_db` instead. That asymmetry is itself worth a test, because
  it means a dependency override silently does nothing for the health probes.
"""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from backend.core.db import Base, get_db
from backend.main import app
from backend.models import Job
from backend.schemas import RawJobPosting

API_KEY = "test"  # conftest forces API_KEY=test
AUTH = {"X-API-Key": API_KEY}
NOW = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _job(**kw) -> Job:
    ident = kw.pop("id")
    return Job(
        id=ident,
        url_hash=kw.pop("url_hash", ident.ljust(64, "u")),
        content_hash=kw.pop("content_hash", ident.ljust(64, "c")),
        source=kw.pop("source", "jsearch"),
        source_id=kw.pop("source_id", f"s-{ident}"),
        title=kw.pop("title", "Python Engineer"),
        company=kw.pop("company", "Acme"),
        location_raw=kw.pop("location_raw", "Bengaluru, India"),
        location_normalized=kw.pop("location_normalized", "Bengaluru, India"),
        employment_type=kw.pop("employment_type", "full_time"),
        experience_level=kw.pop("experience_level", "mid"),
        remote=kw.pop("remote", "hybrid"),
        description=kw.pop("description", "python and fastapi"),
        skills_required=[],
        salary_currency=kw.pop("salary_currency", "INR"),
        apply_url=kw.pop("apply_url", f"https://example.invalid/{ident}"),
        posted_at=kw.pop("posted_at", NOW),
        is_active=kw.pop("is_active", True),
        created_at=kw.pop("created_at", NOW),
        updated_at=kw.pop("updated_at", NOW),
        **kw,
    )


@pytest.fixture
def session(tmp_path):
    # A file-backed SQLite DB, like test_migrations.py uses: an in-memory one
    # gives each thread its own empty database under TestClient's portal thread.
    # check_same_thread=False because dependency_overrides hands the *same*
    # session to a route running on a different thread than the test.
    engine = sa.create_engine(
        f"sqlite:///{tmp_path / 'routes.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    for i in range(6):
        db.add(
            _job(
                id=f"{i:032d}",
                title=f"Python Engineer {i}",
                posted_at=NOW - dt.timedelta(days=i),
                is_active=i != 5,
            )
        )
    db.commit()
    yield db
    db.close()


@pytest.fixture
def client(session, monkeypatch):
    """A TestClient whose routes resolve `get_db` to the seeded SQLite session.

    Embeddings and the vector store are stubbed so `/health` never reaches real
    infra (creating a Qdrant local directory during a unit test is a side effect,
    and its shutdown path spams interpreter-exit noise).
    """
    class _Provider:
        def embed(self, text):
            return [0.0]

    monkeypatch.setattr(
        "backend.services.matching.embeddings.get_embedding_provider", lambda: _Provider()
    )
    monkeypatch.setattr(
        "backend.services.matching.vector_store.get_client", lambda: object()
    )
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def no_adapters(monkeypatch):
    """Stop `/jobs/search` from touching any provider.

    The route's own job is to fan out, normalize, dedup, filter and paginate. The
    adapters have their own tests; leaving them in play here would make these tests
    depend on network mode, keys and fixture data.
    """

    class _StubAdapter:
        def __init__(self, postings):
            self._postings = postings

        async def search(self, query, location=None):
            return list(self._postings)

    def _install(postings=()):
        monkeypatch.setattr(
            "backend.main.get_adapter", lambda _sources=None: _StubAdapter(list(postings))
        )

    return _install


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

def test_data_routes_require_the_api_key(client):
    for method, path in [
        ("post", "/jobs/search"),
        ("get", "/agents/health"),
    ]:
        r = client.request(method, path, json={} if method == "post" else None)
        assert r.status_code == 401, f"{method.upper()} {path} served without a key"


def test_health_is_exempt_from_auth(client, session, monkeypatch):
    """Probes must stay reachable without a key or an orchestrator cannot call them.

    The `get_db` patch is required for `/health/ready` to return 200: neither probe
    takes its session via `Depends`, so `dependency_overrides` does not reach them
    and they would otherwise hit MySQL directly.
    """
    monkeypatch.setattr("backend.main.get_db", lambda: iter([session]))
    assert client.get("/health").status_code == 200
    assert client.get("/health/ready").status_code == 200


# --------------------------------------------------------------------------
# GET /jobs/{job_id}
# --------------------------------------------------------------------------

def test_get_job_returns_the_row(client, session):
    target = session.query(Job).first().id
    r = client.get(f"/jobs/{target}", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["id"] == target


def test_get_job_unknown_id_is_404_with_detail(client):
    r = client.get("/jobs/does-not-exist", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"] == "job not found"


# --------------------------------------------------------------------------
# POST /jobs/search
# --------------------------------------------------------------------------

def test_search_returns_seeded_rows(client, no_adapters):
    no_adapters()
    r = client.post("/jobs/search", json={"query": "python"}, headers=AUTH)
    assert r.status_code == 200
    assert len(r.json()) == 5  # 6 seeded, 1 inactive


def test_search_excludes_inactive_jobs(client, no_adapters, session):
    """The endpoint never serves a deactivated posting.

    This pins the user-facing contract end to end, and is deliberately not a
    test of the SQL `is_active` clause: `apply_hard_filters` excludes inactive
    rows too, so removing the clause would still pass here and only cost a row
    scan. The SQL clause itself is covered in test_filters_sql.py.
    """
    no_adapters()
    ids = {j["id"] for j in client.post("/jobs/search", json={}, headers=AUTH).json()}
    inactive = session.query(Job).filter(Job.is_active.is_(False)).one().id
    assert inactive not in ids


def test_search_orders_newest_first(client, no_adapters):
    no_adapters()
    rows = client.post("/jobs/search", json={}, headers=AUTH).json()
    dates = [r["posted_at"] for r in rows]
    assert dates == sorted(dates, reverse=True)


def test_search_honours_limit(client, no_adapters):
    no_adapters()
    r = client.post("/jobs/search", json={"limit": 2}, headers=AUTH)
    assert len(r.json()) == 2


def test_search_offset_walks_pages(client, no_adapters):
    no_adapters()
    page1 = client.post("/jobs/search", json={"limit": 2, "offset": 0}, headers=AUTH).json()
    page2 = client.post("/jobs/search", json={"limit": 2, "offset": 2}, headers=AUTH).json()
    assert {j["id"] for j in page1}.isdisjoint({j["id"] for j in page2})


def test_search_offset_past_the_end_is_empty_not_an_error(client, no_adapters):
    no_adapters()
    r = client.post("/jobs/search", json={"limit": 2, "offset": 999}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == []


def test_search_applies_the_location_filter(client, no_adapters):
    no_adapters()
    rows = client.post("/jobs/search", json={"location": "atlantis"}, headers=AUTH).json()
    assert rows == []


def test_search_location_us_does_not_500(client, no_adapters):
    """Regression: `us` and `usa` alias each other, so the matcher used to recurse
    until the stack blew and this route returned 500."""
    no_adapters()
    for needle in ("us", "usa", "uk", "in", "india"):
        r = client.post("/jobs/search", json={"location": needle}, headers=AUTH)
        assert r.status_code == 200, f"location={needle} -> {r.status_code}"


@pytest.mark.parametrize("bad", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
def test_search_rejects_out_of_range_pagination(client, bad):
    r = client.post("/jobs/search", json=bad, headers=AUTH)
    assert r.status_code == 422


def test_search_persists_a_new_posting_end_to_end(client, no_adapters, session):
    """Adapter -> normalize -> dedup -> filter -> response, in one request."""
    no_adapters(
        [
            RawJobPosting(
                source="mock",
                source_id="route-test-1",
                title="Rust Engineer",
                company="Example Corp",
                location_raw="Remote",
                employment_type_raw="Full-time",
                description="Systems work in Rust",
                apply_url="https://example.invalid/rust-1",
                posted_at_raw="2026-08-01",
                salary_raw="",
            )
        ]
    )
    r = client.post("/jobs/search", json={"query": "rust"}, headers=AUTH)
    assert r.status_code == 200
    # JobOut exposes `source` but not `source_id`, so assert on the URL it carried.
    assert [j["apply_url"] for j in r.json()] == ["https://example.invalid/rust-1"]
    # And it really landed in the database, not just the response.
    assert session.query(Job).filter(Job.source_id == "route-test-1").count() == 1


def test_search_surfaces_a_broken_adapter_as_a_500(client, monkeypatch):
    """Per-source resilience lives in the adapter's fan-out (`return_exceptions=True`),
    which test_job_adapter.py covers. If the adapter itself is broken, that is a
    server fault and the route must not pretend otherwise."""

    class _Boom:
        async def search(self, query, location=None):
            raise RuntimeError("adapter is broken")

    monkeypatch.setattr("backend.main.get_adapter", lambda _sources=None: _Boom())
    with pytest.raises(RuntimeError, match="adapter is broken"):
        client.post("/jobs/search", json={"query": "python"}, headers=AUTH)


# --------------------------------------------------------------------------
# POST /jobs/{job_id}/verify
# --------------------------------------------------------------------------

def test_verify_unknown_job_is_404(client):
    r = client.post("/jobs/nope/verify", headers=AUTH)
    assert r.status_code == 404


def test_verify_marks_a_confirmed_posting(client, session, monkeypatch):
    job = session.query(Job).filter(Job.is_active.is_(True)).first()

    async def _confirmed(apply_url, title, company):
        from backend.services.sourcing.verification import VerificationResult

        return VerificationResult(verified=True, reason="confirmed live", evidence="200")

    monkeypatch.setattr(
        "backend.services.sourcing.verification.verify_job_posting", _confirmed
    )
    r = client.post(f"/jobs/{job.id}/verify", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["verified"] is True
    assert r.json()["verified_at"] is not None


def test_verify_deactivates_a_closed_posting(client, session, monkeypatch):
    """A posting confirmed closed must stop being served.

    Driven by the structured `posting_closed` flag, not by the reason text — the
    old implementation substring-matched "closed" in `reason`, which meant any
    reworded message silently stopped deactivating, and any Firecrawl
    mis-detection permanently removed a live posting via retention.
    """
    job = session.query(Job).filter(Job.is_active.is_(True)).first()

    async def _closed(apply_url, title, company):
        from backend.services.sourcing.verification import VerificationResult

        return VerificationResult(
            verified=False, reason="posting closed", posting_closed=True
        )

    monkeypatch.setattr(
        "backend.services.sourcing.verification.verify_job_posting", _closed
    )
    r = client.post(f"/jobs/{job.id}/verify", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["is_active"] is False


def test_verify_does_not_deactivate_on_a_soft_failure(client, session, monkeypatch):
    """A network blip is not evidence the job is gone — leave it active."""
    job = session.query(Job).filter(Job.is_active.is_(True)).first()

    async def _unreachable(apply_url, title, company):
        from backend.services.sourcing.verification import VerificationResult

        return VerificationResult(verified=False, reason="timeout", evidence=None)

    monkeypatch.setattr(
        "backend.services.sourcing.verification.verify_job_posting", _unreachable
    )
    r = client.post(f"/jobs/{job.id}/verify", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["is_active"] is True


# --------------------------------------------------------------------------
# /health and /health/ready
# --------------------------------------------------------------------------

def test_health_reports_every_component(client, monkeypatch):
    monkeypatch.setattr("backend.main.get_db", lambda: iter(()))  # unusable -> degraded
    body = client.get("/health").json()
    assert body["status"] in ("healthy", "degraded")
    for component in (
        "database",
        "rate_limit",
        "checkpoint",
        "embedding_model",
        "vector_store",
    ):
        assert component in body["checks"], f"{component} missing from /health"
        assert body["checks"][component]["status"] in ("ok", "degraded", "unknown")


def test_health_is_degraded_when_the_database_is_down(client, monkeypatch):
    """Never reports healthy while the system of record is unreachable."""
    monkeypatch.setattr("backend.main.get_db", lambda: iter(()))
    assert client.get("/health").json()["status"] == "degraded"


def test_health_is_healthy_when_the_database_answers(client, session, monkeypatch):
    """dependency_overrides does NOT reach /health — it calls get_db() directly."""
    monkeypatch.setattr("backend.main.get_db", lambda: iter([session]))
    assert client.get("/health").json()["checks"]["database"]["status"] == "ok"


def test_readiness_is_200_when_the_database_answers(client, session, monkeypatch):
    monkeypatch.setattr("backend.main.get_db", lambda: iter([session]))
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


def test_readiness_is_503_when_the_database_is_down(client, monkeypatch):
    monkeypatch.setattr("backend.main.get_db", lambda: iter(()))
    r = client.get("/health/ready")
    assert r.status_code == 503
    assert "database unavailable" in r.json()["detail"]


def test_agents_health_returns_a_snapshot(client):
    body = client.get("/agents/health", headers=AUTH).json()
    assert isinstance(body, dict)