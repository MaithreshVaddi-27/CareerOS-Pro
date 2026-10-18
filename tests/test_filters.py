"""Tests for filtering functions."""

from backend.models import Job
from backend.schemas import SearchRequest
from backend.services.matching.filters import apply_hard_filters


def make_job(**overrides):
    """Helper to create a job with sensible defaults."""
    import uuid
    from datetime import datetime, timezone

    defaults = {
        "id": str(uuid.uuid4()),
        "url_hash": "dummy_url_hash",
        "content_hash": "dummy_content_hash",
        "title": "Software Engineer",
        "company": "Acme Corp",
        "location_raw": "New York",
        "location_normalized": "New York",
        "employment_type": "full_time",
        "experience_level": "mid",
        "remote": "onsite",
        "description": "Great job",
        "skills_required": ["Python", "SQL"],
        "salary_min": 100000.0,
        "salary_max": 120000.0,
        "salary_currency": "USD",
        "apply_url": "https://example.com/job/1",
        "posted_at": datetime.now(timezone.utc),
        "is_active": True,
    }
    defaults.update(overrides)
    return Job(**defaults)


def test_employment_type_filter():
    job = make_job(employment_type="full_time")
    # Should pass when no filter or matching filter
    req = SearchRequest(query="Software Engineer")
    assert apply_hard_filters([job], req) == [job]

    req = SearchRequest(query="Software Engineer", employment_type="full_time")
    assert apply_hard_filters([job], req) == [job]

    # Should fail when filter doesn't match
    req = SearchRequest(query="Software Engineer", employment_type="part_time")
    assert apply_hard_filters([job], req) == []

    req = SearchRequest(query="Software Engineer", employment_type="internship")
    assert apply_hard_filters([job], req) == []


def test_experience_level_filter():
    job = make_job(experience_level="senior")
    req = SearchRequest(query="Software Engineer")
    assert apply_hard_filters([job], req) == [job]

    req = SearchRequest(query="Software Engineer", experience_level="senior")
    assert apply_hard_filters([job], req) == [job]

    req = SearchRequest(query="Software Engineer", experience_level="junior")
    assert apply_hard_filters([job], req) == []


def test_remote_filter():
    job_on_site = make_job(remote="onsite")
    job_remote = make_job(remote="remote")
    job_hybrid = make_job(remote="hybrid")

    # On-site job
    req = SearchRequest(query="Software Engineer")
    assert apply_hard_filters([job_on_site], req) == [job_on_site]

    req = SearchRequest(query="Software Engineer", remote_only=False)
    assert apply_hard_filters([job_on_site], req) == [job_on_site]

    req = SearchRequest(query="Software Engineer", remote_only=True)
    assert apply_hard_filters([job_on_site], req) == []

    # Remote job
    req = SearchRequest(query="Software Engineer", remote_only=False)
    assert apply_hard_filters([job_remote], req) == [job_remote]

    req = SearchRequest(query="Software Engineer", remote_only=True)
    assert apply_hard_filters([job_remote], req) == [job_remote]

    # Hybrid job
    req = SearchRequest(query="Software Engineer", remote_only=False)
    assert apply_hard_filters([job_hybrid], req) == [job_hybrid]

    req = SearchRequest(query="Software Engineer", remote_only=True)
    assert apply_hard_filters([job_hybrid], req) == []


def test_salary_filter():
    job = make_job(salary_min=50000.0, salary_max=70000.0)
    # No filter
    req = SearchRequest(query="Software Engineer")
    assert apply_hard_filters([job], req) == [job]

    # Min salary filter (only implemented filter)
    req = SearchRequest(query="Software Engineer", min_salary=40000.0)
    assert apply_hard_filters([job], req) == [job]  # job max 70k >= 40k

    req = SearchRequest(query="Software Engineer", min_salary=60000.0)
    assert apply_hard_filters([job], req) == [job]  # job max 70k >= 60k

    req = SearchRequest(query="Software Engineer", min_salary=70000.0)
    assert apply_hard_filters([job], req) == [job]  # job max 70k >= 70k (at bound)

    req = SearchRequest(query="Software Engineer", min_salary=75000.0)
    assert apply_hard_filters([job], req) == []  # job max 70k < 75k

    # No min_salary filter (should pass if query matches)
    req = SearchRequest(query="Software Engineer", min_salary=None)
    assert apply_hard_filters([job], req) == [job]

    # Edge case: very high min_salary
    req = SearchRequest(query="Software Engineer", min_salary=1000000.0)
    assert apply_hard_filters([job], req) == []  # job max 70k < 1M

    # Note: max_salary field exists in SearchRequest but is not used in apply_hard_filters
    # This is a known limitation - only min_salary filtering is implemented


def test_combined_filters():
    job = make_job(
        employment_type="full_time",
        experience_level="senior",
        remote="remote",
        salary_min=90000.0,
        salary_max=110000.0
    )
    # All match
    req = SearchRequest(
        query="Software Engineer",
        employment_type="full_time",
        experience_level="senior",
        remote_only=False,
        min_salary=80000.0,
        max_salary=120000.0,
        location=None
    )
    assert apply_hard_filters([job], req) == [job]

    # One fails - employment type
    req = SearchRequest(
        query="Software Engineer",
        employment_type="part_time",  # fails
        experience_level="senior",
        remote_only=False,
        min_salary=80000.0,
        max_salary=120000.0,
        location=None
    )
    assert apply_hard_filters([job], req) == []