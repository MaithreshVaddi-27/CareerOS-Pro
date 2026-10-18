import os

import pytest

# Module-level settings (backend/core/config.py calls get_settings() at *import*
# time in several modules) need these env vars before pytest even collects
# the test files - an autouse fixture alone runs too late and every test
# module fails to import with a pydantic ValidationError.
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("API_KEY", "test")
os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_PORT", "3306")
os.environ.setdefault("MYSQL_DATABASE", "test")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("JOB_API_MODE", "mock")


@pytest.fixture(autouse=True)
def set_env_vars(monkeypatch):
    """Set required environment variables for tests."""
    monkeypatch.setenv("MYSQL_PASSWORD", "test")
    monkeypatch.setenv("API_KEY", "test")
    monkeypatch.setenv("MYSQL_HOST", "localhost")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("MYSQL_DATABASE", "test")
    monkeypatch.setenv("MYSQL_USER", "test")
    # For job adapter tests
    monkeypatch.setenv("JOB_API_MODE", "mock")


@pytest.fixture(autouse=True)
def _netguard_does_not_touch_dns(monkeypatch):
    """Pin every outbound target to a public IP for the duration of a test.

    backend.core.netguard resolves the host to reject private/link-local
    addresses. Most unit tests drive a mocked httpx transport and assert on
    scraper/verification logic, so a real getaddrinfo would make them depend
    on the network (and on acme.com having DNS records). Stub it to one public
    address so the guard passes and the rest of the test is unchanged.

    Tests for the guard's own behaviour patch this fixture out — see
    tests/test_netguard.py.
    """
    monkeypatch.setattr(
        "backend.core.netguard.socket.getaddrinfo",
        lambda *a, **kw: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )