"""Tests for deduplication functions."""

from unittest.mock import Mock, patch

from sqlalchemy.orm import Session

from backend.models import Job
from backend.schemas import RawJobPosting
from backend.services.dedup import upsert_job
from backend.services.normalize import hash_content, hash_url


def test_hash_url():
    # Test that equivalent URLs produce same hash (query params are stripped)
    url1 = "https://example.com/job?utm_source=test&ref=home"
    url2 = "https://example.com/job?ref=home&utm_source=test"
    url3 = "https://example.com/job"
    url4 = "https://example.com/job/"  # with trailing slash

    h1 = hash_url(url1)
    h2 = hash_url(url2)
    h3 = hash_url(url3)
    h4 = hash_url(url4)

    # All should produce the same hash because:
    # - Query parameters are stripped entirely
    # - Trailing slashes are stripped from path
    # - Scheme and host are lowercased
    assert h1 == h2  # Different query param order
    assert h1 == h3  # With and without query params
    assert h1 == h4  # With and without trailing slash
    assert len(h1) == 64  # SHA256 length

    # Test that different paths produce different hashes
    url5 = "https://example.com/different"
    h5 = hash_url(url5)
    assert h1 != h5  # Different path should produce different hash


def test_hash_content():
    # Test that equivalent content produces same hash
    content1 = ("Software Engineer", "Acme Corp", "New York")
    content2 = ("software engineer", "acme corp", "new york")
    content3 = ("Software Engineer", "Different Corp", "New York")

    h1 = hash_content(*content1)
    h2 = hash_content(*content2)
    h3 = hash_content(*content3)

    assert h1 == h2  # Case insensitive
    assert h1 != h3  # Different company
    assert len(h1) == 64  # SHA256 length


def test_upsert_job_deduplication():
    """Test that dedup works correctly with url_hash and content_hash."""
    # Create a mock database session
    mock_db = Mock(spec=Session)

    # Mock the find_duplicate to return None for first call (no duplicate)
    # and return a mock Job for second call (duplicate found)
    mock_existing_job = Mock(spec=Job)
    mock_existing_job.source_id = "job-001"

    with patch('backend.services.dedup.find_duplicate') as mock_find_duplicate:
        mock_find_duplicate.side_effect = [None, mock_existing_job]

        job1 = RawJobPosting(
            source="test",
            source_id="job-001",
            title="Software Engineer",
            company="Acme Corp",
            location_raw="New York",
            employment_type_raw="Full-time",
            description="Great job",
            apply_url="https://example.com/job/1",
            posted_at_raw="2026-08-25",
            salary_raw="$100,000"
        )

        job2 = RawJobPosting(
            source="test",
            source_id="job-002",  # Different source_id
            title="software engineer",  # Different case
            company="acme corp",  # Different case
            location_raw="new york",  # Different case
            employment_type_raw="FULL-TIME",  # Different case
            description="Great job",
            apply_url="https://EXAMPLE.COM/JOB/1?utm_source=test",  # Different case & tracking
            posted_at_raw="2026-08-25",
            salary_raw="₹100,000"  # Different currency format
        )

        # Convert to normalized job for dedup functions
        from backend.services.normalize import normalize_job
        normalized_job1 = normalize_job(job1)
        normalized_job2 = normalize_job(job2)

        # First job should be stored (no duplicate found)
        result1, created1 = upsert_job(mock_db, normalized_job1)
        assert created1 is True  # First job should be created
        assert mock_find_duplicate.call_count == 1  # Called once for first job

        # Second job should be considered duplicate and return the existing one
        result2, created2 = upsert_job(mock_db, normalized_job2)
        assert result2 == mock_existing_job
        assert created2 is False  # Not created because it's a duplicate
        assert mock_find_duplicate.call_count == 2  # Called twice total


def test_upsert_job_no_duplicate():
    """Test that different jobs are not considered duplicates."""
    # Create a mock database session
    mock_db = Mock(spec=Session)

    # Mock find_duplicate to return None (no duplicates found)
    with patch('backend.services.dedup.find_duplicate') as mock_find_duplicate:
        mock_find_duplicate.return_value = None

        job1 = RawJobPosting(
            source="test",
            source_id="job-001",
            title="Software Engineer",
            company="Acme Corp",
            location_raw="New York",
            employment_type_raw="Full-time",
            description="Great job",
            apply_url="https://example.com/job/1",
            posted_at_raw="2026-08-25",
            salary_raw="$100,000"
        )

        job2 = RawJobPosting(
            source="test",
            source_id="job-002",
            title="Product Manager",  # Different title
            company="Acme Corp",
            location_raw="New York",
            employment_type_raw="Full-time",
            description="Great job",
            apply_url="https://example.com/job/2",  # Different URL
            posted_at_raw="2026-08-25",
            salary_raw="$120,000"
        )

        # Convert to normalized job for dedup functions
        from backend.services.normalize import normalize_job
        normalized_job1 = normalize_job(job1)
        normalized_job2 = normalize_job(job2)

        # Both jobs should be stored (no duplicates found)
        result1, created1 = upsert_job(mock_db, normalized_job1)
        result2, created2 = upsert_job(mock_db, normalized_job2)

        # Both should be created (no duplicates found)
        assert created1 is True
        assert created2 is True
        # Should have called find_duplicate twice
        assert mock_find_duplicate.call_count == 2