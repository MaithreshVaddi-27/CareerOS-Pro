"""Tests for job API adapter."""


import pytest

from backend.core.config import get_settings
from backend.schemas import RawJobPosting
from backend.services.job_api_adapter import (
    JSearchAdapter,
    MockJobAdapter,
    MultiSourceAdapter,
    SafeSingleSourceAdapter,
    _CompanyScraperAdapter,
    get_adapter,
)


def test_mock_adapter():
    adapter = MockJobAdapter()
    import asyncio
    jobs = asyncio.run(adapter.search("engineer", "New York"))
    assert len(jobs) == 2
    assert all(isinstance(job, RawJobPosting) for job in jobs)
    assert jobs[0].source == "mock"
    assert jobs[0].title == "engineer Engineer"


def test_get_adapter_mock_mode(monkeypatch):
    monkeypatch.setenv("JOB_API_MODE", "mock")
    get_settings.cache_clear()
    adapter = get_adapter()
    assert isinstance(adapter, MockJobAdapter)


def test_get_adapter_live_mode_no_sources(monkeypatch):
    monkeypatch.setenv("JOB_API_MODE", "live")
    monkeypatch.setenv("JOB_SOURCES", "")  # No sources
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="No job sources configured for live mode"):
        get_adapter()


def test_get_adapter_live_mode_with_jsearch(monkeypatch):
    monkeypatch.setenv("JOB_API_MODE", "live")
    monkeypatch.setenv("JOB_SOURCES", "jsearch")
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    get_settings.cache_clear()
    adapter = get_adapter()
    # With a single source, it's wrapped in SafeSingleSourceAdapter
    assert isinstance(adapter, SafeSingleSourceAdapter)
    assert isinstance(adapter._adapter, JSearchAdapter)
    # Cleanup
    get_settings.cache_clear()


def test_get_adapter_live_mode_multiple_sources(monkeypatch):
    monkeypatch.setenv("JOB_API_MODE", "live")
    monkeypatch.setenv("JOB_SOURCES", "jsearch,adzuna")
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("ADZUNA_APP_ID", "test-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-key")
    get_settings.cache_clear()
    adapter = get_adapter()
    assert isinstance(adapter, MultiSourceAdapter)
    get_settings.cache_clear()


def test_safe_single_source_adapter_wraps():
    # We'll test that SafeSingleSourceAdapter wraps another adapter and delegates
    inner = MockJobAdapter()
    wrapped = SafeSingleSourceAdapter(inner)
    import asyncio
    jobs = asyncio.run(wrapped.search("test", "location"))
    assert len(jobs) == 2  # same as mock
    assert jobs[0].source == "mock"


def test_multi_source_adapter_combines():
    # Create two mock adapters (they each return 2 jobs)
    adapter1 = MockJobAdapter()
    adapter2 = MockJobAdapter()
    # We need to patch them to return different source identifiers to distinguish
    # But for simplicity, we just test that it returns jobs (will be 4 total from two adapters)
    multi = MultiSourceAdapter([adapter1, adapter2])
    import asyncio
    jobs = asyncio.run(multi.search("test", "location"))
    # Each mock adapter returns 2 jobs, so total 4
    assert len(jobs) == 4
    # They should all be from mock source (since both are MockJobAdapter)
    assert all(job.source == "mock" for job in jobs)


def test_company_scraper_adapter():
    adapter = _CompanyScraperAdapter()
    # This adapter returns empty list because we haven't set up company scraper patterns
    import asyncio
    jobs = asyncio.run(adapter.search("engineer", "New York"))
    assert jobs == []  # No company patterns configured