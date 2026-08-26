import os

import pytest

# Module-level settings (backend/config.py calls get_settings() at *import*
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