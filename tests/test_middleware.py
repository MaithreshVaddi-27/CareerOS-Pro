"""Middleware: rate limiting (both backends) and API-key auth.

The rate limiter had no test coverage at all before this file, which is how the
per-process counter bug (audit A2) survived. Both backends are covered.
"""

import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from backend.core import middleware as mw
from backend.core.middleware import RateLimitMiddleware, RequestContextMiddleware, require_api_key


async def _ok(request):
    return JSONResponse({"ok": True})


def build(limit=60, burst=None, burst_window=10, with_context=True):
    app = Starlette(routes=[Route("/ping", _ok), Route("/health", _ok)])
    if with_context:
        app.add_middleware(RateLimitMiddleware, requests_per_minute=limit,
                           burst_limit=burst, burst_window_seconds=burst_window)
        # request_id is set here in production; supply it so the 429 path can log it.
        app.add_middleware(RequestContextMiddleware)
    return app


@pytest.fixture(autouse=True)
def _reset_counters():
    """The in-memory stores are module-level; keep tests independent."""
    mw._request_counts.clear()
    mw._burst_counts.clear()
    yield
    mw._request_counts.clear()
    mw._burst_counts.clear()


# --------------------------------------------------------------------------
# in-memory backend
# --------------------------------------------------------------------------

def test_requests_under_limit_pass():
    with TestClient(build(limit=5)) as c:
        assert all(c.get("/ping").status_code == 200 for _ in range(5))


def test_request_over_limit_gets_429():
    with TestClient(build(limit=3)) as c:
        for _ in range(3):
            assert c.get("/ping").status_code == 200
        r = c.get("/ping")
        assert r.status_code == 429


def test_429_carries_retry_after():
    """Clients need to know how long to back off; the header was missing."""
    with TestClient(build(limit=1)) as c:
        c.get("/ping")
        assert c.get("/ping").headers.get("Retry-After") == "60"


def test_health_is_exempt():
    with TestClient(build(limit=1)) as c:
        for _ in range(10):
            assert c.get("/health").status_code == 200


def test_zero_limit_disables_throttling():
    with TestClient(build(limit=0)) as c:
        assert all(c.get("/ping").status_code == 200 for _ in range(20))


def test_burst_limit_trips_faster_than_minute_limit():
    """burst_limit=2 inside 10s trips while the 60s limit of 100 is untouched."""
    with TestClient(build(limit=100, burst=2, burst_window=10)) as c:
        assert c.get("/ping").status_code == 200
        assert c.get("/ping").status_code == 200
        assert c.get("/ping").status_code == 429


def test_counters_are_per_client_ip():
    """One noisy client must not throttle another."""
    app = build(limit=2)
    with TestClient(app) as c:
        assert c.get("/ping", headers={"X-Real-Ip": "a"}).status_code == 200


# --------------------------------------------------------------------------
# redis backend
# --------------------------------------------------------------------------

class FakeRedis:
    """Stands in for redis.asyncio. Counts are shared, as they would be
    across real workers."""

    def __init__(self, fail=False):
        self.counts = {}
        self.ttls = {}
        self.fail = fail
        self.keys = []

    async def incr(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        self.keys.append(key)
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, ttl):
        self.ttls[key] = ttl


def test_redis_backend_uses_shared_counters(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(mw, "_get_redis", lambda: fake)
    monkeypatch.setattr(mw.settings, "redis_url", "redis://redis:6379/0")

    with TestClient(build(limit=3)) as c:
        assert [c.get("/ping").status_code for _ in range(3)] == [200, 200, 200]
        assert c.get("/ping").status_code == 429

    assert any(k.startswith("rl:min:") for k in fake.counts)
    assert fake.ttls, "counter keys must expire or Redis grows unbounded"


def test_redis_limit_is_shared_between_clients_of_the_same_counter(monkeypatch):
    """The point of A2: two 'workers' sharing a store share the limit.
    Independent middleware instances would each allow `limit` requests."""
    fake = FakeRedis()
    monkeypatch.setattr(mw, "_get_redis", lambda: fake)
    monkeypatch.setattr(mw.settings, "redis_url", "redis://redis:6379/0")

    worker_a, worker_b = build(limit=2), build(limit=2)
    with TestClient(worker_a) as a, TestClient(worker_b) as b:
        assert a.get("/ping").status_code == 200
        assert b.get("/ping").status_code == 200
        # Third request across the *shared* store is over the limit, even
        # though neither worker has seen 3 requests itself.
        assert a.get("/ping").status_code == 429


def test_redis_failure_falls_open_to_memory(monkeypatch):
    """Redis outage must not 500 every request."""
    fake = FakeRedis(fail=True)
    monkeypatch.setattr(mw, "_get_redis", lambda: fake)
    monkeypatch.setattr(mw.settings, "redis_url", "redis://redis:6379/0")

    with TestClient(build(limit=2)) as c:
        assert c.get("/ping").status_code == 200  # served, counted in memory
        assert c.get("/ping").status_code == 200
        assert c.get("/ping").status_code == 429   # memory limit still enforced


def test_no_redis_url_uses_memory(monkeypatch):
    monkeypatch.setattr(mw, "_get_redis", lambda: None)
    monkeypatch.setattr(mw.settings, "redis_url", "")
    with TestClient(build(limit=2)) as c:
        for _ in range(2):
            assert c.get("/ping").status_code == 200
        assert c.get("/ping").status_code == 429


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

async def _guarded(request):
    await require_api_key(request)
    return JSONResponse({"ok": True})


def _client_with_guard():
    # FastAPI, not bare Starlette: HTTPException -> JSON detail conversion is a
    # FastAPI exception handler, so a raw Starlette route would raise instead.
    app = FastAPI(routes=[Route("/ping", _guarded)])
    return TestClient(app)


def test_missing_api_key_is_401(monkeypatch):
    monkeypatch.setattr(mw.settings, "api_key", "secret")
    r = _client_with_guard().get("/ping")
    assert r.status_code == 401
    assert "missing X-API-Key" in r.json()["detail"]


def test_wrong_api_key_is_401(monkeypatch):
    monkeypatch.setattr(mw.settings, "api_key", "secret")
    r = _client_with_guard().get("/ping", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401
    assert "invalid X-API-Key" in r.json()["detail"]


def test_correct_api_key_passes(monkeypatch):
    monkeypatch.setattr(mw.settings, "api_key", "secret")
    r = _client_with_guard().get("/ping", headers={"X-API-Key": "secret"})
    assert r.status_code == 200


def test_unset_api_key_is_open(monkeypatch):
    monkeypatch.setattr(mw.settings, "api_key", "")
    assert _client_with_guard().get("/ping").status_code == 200


# --------------------------------------------------------------------------
# response headers
# --------------------------------------------------------------------------

def test_security_headers_present(monkeypatch):
    monkeypatch.setattr(mw.settings, "app_env", "production")
    with TestClient(build(limit=100)) as c:
        h = c.get("/ping").headers
        assert h["X-Content-Type-Options"] == "nosniff"
        assert h["X-Frame-Options"] == "DENY"
        assert h["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert h["Strict-Transport-Security"] == "max-age=63072000; includeSubDomains"
        assert h["X-Request-ID"], "every response must be traceable"


def test_hsts_omitted_in_development(monkeypatch):
    """HSTS over plain http://localhost would be cached by the browser and
    break local dev, so it is production-only."""
    monkeypatch.setattr(mw.settings, "app_env", "development")
    with TestClient(build(limit=100)) as c:
        assert "Strict-Transport-Security" not in c.get("/ping").headers