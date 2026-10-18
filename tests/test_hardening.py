"""Guards that must not regress.

Each test here pins a behaviour that was previously absent and whose absence was
a production defect: the schema command that silently created nothing, the API
that served unauthenticated traffic while claiming to be in production, and the
outbound-fetch guard that turns attacker-supplied URLs into an SSRF primitive.
"""


import os
import subprocess
import sys

import pytest

from backend.core import netguard
from backend.core.netguard import BlockedTarget, assert_public_https

# ---------------------------------------------------------------------------
# Outbound URL guard
# ---------------------------------------------------------------------------


def _resolve(*addrs):
    """Force getaddrinfo to return `addrs`, bypassing the conftest stub."""
    import backend.core.netguard as ng

    original = ng.socket.getaddrinfo

    def fake(host, port, *a, **kw):
        return [
            (2, 1, 6, "", (addr, port or 443)) for addr in addrs
        ]

    ng.socket.getaddrinfo = fake
    return lambda: setattr(ng.socket, "getaddrinfo", original)


@pytest.mark.parametrize(
    "url,why",
    [
        ("http://example.com/careers", "plaintext is a free MITM on a hostile network"),
        ("https://localhost/x", "loopback reaches the container itself"),
        ("https://metadata.google.internal/x", "cloud metadata service"),
        ("https://169.254.169.254/latest/meta-data/", "AWS/GCP IMDS"),
        ("ftp://example.com/x", "non-HTTP scheme"),
    ],
)
def test_guard_blocks_non_https_and_named_hosts(url, why):
    with pytest.raises(BlockedTarget):
        assert_public_https(url)


@pytest.mark.parametrize(
    "addr",
    ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.17.0.2", "169.254.169.254", "::1"],
)
def test_guard_blocks_every_non_public_resolution(addr):
    restore = _resolve(addr)
    try:
        with pytest.raises(BlockedTarget):
            assert_public_https("https://evil.example/careers")
    finally:
        restore()


def test_guard_allows_a_public_https_target():
    restore = _resolve("93.184.216.34")
    try:
        assert_public_https("https://evil.example/careers")  # does not raise
    finally:
        restore()


def test_guard_blocks_when_any_resolved_address_is_private():
    """DNS commonly returns several A records. One private answer is enough to
    make the target unsafe, so the check must not stop at the first."""
    restore = _resolve("93.184.216.34", "169.254.169.254")
    try:
        with pytest.raises(BlockedTarget):
            assert_public_https("https://split-horizon.example/careers")
    finally:
        restore()


def test_scraper_refuses_a_redirect_hop_into_the_private_network():
    """follow_redirects=True means the first URL is not the only URL connected to.

    httpx fires the "request" hook per hop, so the guard covers the redirect.
    """
    import asyncio

    import httpx

    from backend.services.sourcing.company_scraper import _guard_request

    req = httpx.Request("GET", "http://169.254.169.254/latest/meta-data/")
    with pytest.raises(BlockedTarget):
        asyncio.run(_guard_request(req))


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------


def test_documented_schema_command_actually_creates_tables(tmp_path):
    """Regression guard for the worst bug this suite ever had.

    Every deploy doc told operators to run a `create_all` command. Because
    backend.core.db does not import backend.models, that command created ZERO
    tables and exited 0 — a deploy that looked fine and had no schema. Every
    other test imports backend.models first, which registered the model as a
    side effect and hid the bug, so this runs the documented form in a clean
    subprocess.
    """
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{tmp_path / 'deploy.db'}",
        "MYSQL_PASSWORD": "test",
        "API_KEY": "test",
    }
    result = subprocess.run(
        [sys.executable, "-c", "from backend.core.db import create_all; create_all()"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr

    from sqlalchemy import create_engine, inspect

    tables = inspect(create_engine(env["DATABASE_URL"])).get_table_names()
    assert "jobs" in tables, f"schema command created {tables}"


def test_schema_command_adds_indexes_to_an_existing_table(tmp_path):
    """Regression guard for the same class of bug, one level down.

    `create_all()` creates missing tables but leaves an existing table entirely
    alone — indexes included. So adding `index=True` to a model and re-running
    the documented deploy command changed nothing, and the missing index only
    surfaced later as a slow query. The live local database had been missing
    six of the nine declared indexes because of exactly this.

    Builds a table first with no indexes, then runs the command again and
    asserts every index the models declare now exists.
    """
    url = f"sqlite:///{tmp_path / 'existing.db'}"
    env = {**os.environ, "DATABASE_URL": url, "MYSQL_PASSWORD": "t", "API_KEY": "t"}

    # First pass: table created by the models, so indexes come along with it.
    first = subprocess.run(
        [sys.executable, "-c", "from backend.core.db import create_all; create_all()"],
        capture_output=True, text=True, env=env,
    )
    assert first.returncode == 0, first.stderr

    # Simulate the drifted database: drop the indexes, leave the table.
    import sqlalchemy
    from sqlalchemy import create_engine, inspect, text

    from backend.models.job import Job

    engine = create_engine(url)
    declared = sorted(i.name for i in Job.__table__.indexes)
    assert declared, "models declare no indexes — test is vacuous"
    with engine.begin() as conn:
        for name in declared:
            conn.execute(text(f"DROP INDEX {name}"))
    gone = [i["name"] for i in inspect(engine).get_indexes("jobs")]
    assert not gone, f"could not drop indexes, still present: {gone}"

    # Second pass: the documented command must put them back.
    second = subprocess.run(
        [sys.executable, "-c", "from backend.core.db import create_all; create_all()"],
        capture_output=True, text=True, env=env,
    )
    assert second.returncode == 0, second.stderr

    restored = sorted(i["name"] for i in sqlalchemy.inspect(engine).get_indexes("jobs"))
    assert restored == declared, f"missing {sorted(set(declared) - set(restored))}"

    # And it must be safe to run again.
    third = subprocess.run(
        [sys.executable, "-c", "from backend.core.db import create_all; create_all()"],
        capture_output=True, text=True, env=env,
    )
    assert third.returncode == 0, f"create_all is not idempotent: {third.stderr}"


# ---------------------------------------------------------------------------
# Production auth posture
# ---------------------------------------------------------------------------


def test_production_refuses_to_start_without_an_api_key():
    """APP_ENV=production with an empty API_KEY served every route to anyone who
    could reach port 8000, because require_api_key is a no-op on an empty key
    and .env.example ships one."""
    env = {
        **os.environ,
        "APP_ENV": "production",
        "API_KEY": "",
        "MYSQL_PASSWORD": "test",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import backend.main"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0
    assert "API_KEY" in result.stderr


def test_production_still_starts_when_a_key_is_present():
    env = {
        **os.environ,
        "APP_ENV": "production",
        "API_KEY": "a-real-key",
        "MYSQL_PASSWORD": "test",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import backend.main"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr


def test_development_still_starts_without_a_key():
    """An empty key is a legitimate local-dev posture and must stay one."""
    env = {
        **os.environ,
        "APP_ENV": "development",
        "API_KEY": "",
        "MYSQL_PASSWORD": "test",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import backend.main"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


def test_health_detail_does_not_leak_a_password():
    from backend.main import _safe_detail

    exc = RuntimeError("(pymysql.err.OperationalError) password: hunter2 at 10.0.0.4")
    detail = _safe_detail(exc)
    assert "hunter2" not in detail
    assert "RuntimeError" in detail


def test_health_detail_is_a_single_bounded_line():
    from backend.main import _safe_detail

    detail = _safe_detail(RuntimeError("line one\nline two\n" + "x" * 5000))
    assert "\n" not in detail
    assert len(detail) <= 200


def test_adzuna_credentials_are_scrubbed_before_the_agent_monitor():
    """Adzuna takes app_id/app_key as query params and 401s on a bad key, and
    httpx puts the whole URL in the error string — which /agents/health returns.
    """
    from backend.services.sourcing.job_api_adapter import _redact

    raw = (
        "Client error '401 Unauthorized' for url "
        "'https://api.adzuna.com/v1/api/jobs/in/search/1"
        "?app_id=SECRET_ID&app_key=SECRET_KEY&what=python'"
    )
    out = _redact(raw)
    assert "SECRET_ID" not in out
    assert "SECRET_KEY" not in out
    assert "api.adzuna.com" in out  # keep the diagnostic, drop the credential