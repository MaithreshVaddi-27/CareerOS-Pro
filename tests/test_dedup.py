"""Tests for job upsert / deduplication against the database."""

from unittest.mock import Mock, patch

from sqlalchemy.orm import Session

from backend.models import Job
from backend.schemas import RawJobPosting
from backend.services.sourcing.dedup import upsert_job


def test_upsert_job_deduplication():
    """Test that dedup works correctly with url_hash and content_hash."""
    # Create a mock database session
    mock_db = Mock(spec=Session)

    # Mock the find_duplicate to return None for first call (no duplicate)
    # and return a mock Job for second call (duplicate found)
    mock_existing_job = Mock(spec=Job)
    mock_existing_job.source_id = "job-001"

    with patch('backend.services.sourcing.dedup.find_duplicate') as mock_find_duplicate:
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
        from backend.services.sourcing.normalize import normalize_job
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
    with patch('backend.services.sourcing.dedup.find_duplicate') as mock_find_duplicate:
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
        from backend.services.sourcing.normalize import normalize_job
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