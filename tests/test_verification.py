"""Tests for verification service."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.services.sourcing.verification import (
    _firecrawl_scrape,
    _http_head_check,
    _looks_like_dead_posting,
    verify_job_posting,
)


def test_looks_like_dead_posting():
    """Test detection of dead job postings from markdown content."""
    # Should detect various dead posting markers
    assert _looks_like_dead_posting("This position has been filled")
    assert _looks_like_dead_posting("We are no longer accepting applications")
    assert _looks_like_dead_posting("Job not found")
    assert _looks_like_dead_posting("This posting has expired")
    assert _looks_like_dead_posting("This job is closed")

    # Should not flag normal content
    assert not _looks_like_dead_posting("Software Engineer position at Tech Corp")
    assert not _looks_like_dead_posting("We are hiring for multiple roles")
    assert not _looks_like_dead_posting("")
    assert not _looks_like_dead_posting("Normal job description text")


@pytest.mark.asyncio
async def test_http_head_check_success():
    """Test successful HTTP HEAD check."""
    with patch('backend.services.sourcing.verification.get_http_client') as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Mock successful HEAD response
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_client.head.return_value = mock_response

        result = await _http_head_check("https://example.com/job")
        assert result
        mock_client.head.assert_called_once_with("https://example.com/job")


@pytest.mark.asyncio
async def test_http_head_check_405_fallback():
    """Test HEAD 405 falls back to GET."""
    with patch('backend.services.sourcing.verification.get_http_client') as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Mock HEAD returning 405, GET returning 200
        mock_head_response = AsyncMock()
        mock_head_response.status_code = 405
        mock_get_response = AsyncMock()
        mock_get_response.status_code = 200

        mock_client.head.return_value = mock_head_response
        mock_client.get.return_value = mock_get_response

        result = await _http_head_check("https://example.com/job")
        assert result
        mock_client.head.assert_called_once_with("https://example.com/job")
        mock_client.get.assert_called_once_with("https://example.com/job")


@pytest.mark.asyncio
async def test_http_head_check_403_fallback():
    """Test HEAD 403 falls back to GET with User-Agent."""
    with patch('backend.services.sourcing.verification.get_http_client') as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Mock HEAD returning 403, GET returning 200 with User-Agent
        mock_head_response = AsyncMock()
        mock_head_response.status_code = 403
        mock_get_response = AsyncMock()
        mock_get_response.status_code = 200

        mock_client.head.return_value = mock_head_response
        mock_client.get.return_value = mock_get_response

        result = await _http_head_check("https://example.com/job")
        assert result
        mock_client.head.assert_called_once_with("https://example.com/job")
        mock_client.get.assert_called_once_with(
            "https://example.com/job",
            headers={"User-Agent": "CareerOS/0.1"}
        )


@pytest.mark.asyncio
async def test_http_head_check_failure():
    """Test HTTP HEAD check failure returns False."""
    with patch('backend.services.sourcing.verification.get_http_client') as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Mock HTTP error - need to raise httpx.HTTPError specifically
        mock_client.head.side_effect = httpx.HTTPError("Network error")

        result = await _http_head_check("https://example.com/job")
        assert not result


@pytest.mark.asyncio
async def test_firecrawl_scrape_no_api_key():
    """Test Firecrawl scrape returns None when no API key."""
    with patch('backend.services.sourcing.verification.settings') as mock_settings:
        mock_settings.firecrawl_api_key = ""

        result = await _firecrawl_scrape("https://example.com/job")
        assert result is None


@pytest.mark.asyncio
async def test_verify_job_posting_unreachable():
    """Test verification fails when URL is unreachable."""
    with patch('backend.services.sourcing.verification._http_head_check') as mock_head_check:
        mock_head_check.return_value = False

        result = await verify_job_posting(
            "https://example.com/job",
            "Software Engineer",
            "Tech Corp"
        )

        assert not result.verified
        assert result.reason == "apply_url not reachable"


@pytest.mark.asyncio
async def test_verify_job_posting_no_firecrawl_key():
    """Test verification when reachable but no Firecrawl key."""
    with patch('backend.services.sourcing.verification._http_head_check') as mock_head_check, \
         patch('backend.services.sourcing.verification.settings') as mock_settings:

        mock_head_check.return_value = True
        mock_settings.firecrawl_api_key = ""

        result = await verify_job_posting(
            "https://example.com/job",
            "Software Engineer",
            "Tech Corp"
        )

        assert not result.verified
        assert result.reason == "reachable, no FIRECRAWL_API_KEY to confirm content"