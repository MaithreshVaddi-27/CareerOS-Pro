"""Tests for normalization functions."""

from backend.services.normalize import (
    hash_content,
    hash_url,
    normalize_url,
    parse_employment_type,
    parse_experience_level,
    parse_location,
    parse_posted_at,
    parse_salary,
)


def test_normalize_url():
    assert normalize_url("https://example.com/path?utm_source=test#fragment") == "https://example.com/path"
    assert normalize_url("HTTP://Example.COM/PATH/") == "http://example.com/PATH"  # scheme lowercased, netloc lowercased, path preserves case
    assert normalize_url("https://user:pass@example.com/path") == "https://example.com/path"




def test_hash_url():
    # Equivalent URLs must collapse to one hash: query strings and trailing
    # slashes are noise, but a different path is a different job.
    variants = [
        "https://example.com/job",
        "https://example.com/job/",
        "https://example.com/job?utm_source=test&ref=home",
        "https://example.com/job?ref=home&utm_source=test",
    ]
    hashes = {hash_url(u) for u in variants}
    assert len(hashes) == 1
    assert len(hashes.pop()) == 64  # sha256 hex
    assert hash_url("https://example.com/different") != hash_url(
        "https://example.com/job"
    )


def test_hash_content():
    assert hash_content("Software Engineer", "Acme Corp", "New York") == hash_content(
        "software engineer", "acme corp", "new york"
    )  # case insensitive
    assert hash_content("Software Engineer", "Acme", "NY") != hash_content(
        "Software Engineer", "Different Corp", "NY"
    )
    assert len(hash_content("a", "b", "c")) == 64


def test_parse_location():
    assert parse_location("Remote") == ("Remote", "remote")
    assert parse_location("Work From Home") == ("Work From Home", "remote")
    assert parse_location("Hybrid") == ("Hybrid", "hybrid")
    assert parse_location("On-Site") == ("On-Site", "onsite")
    assert parse_location("") == ("unknown", "unknown")
    assert parse_location("Bengaluru, India") == ("Bengaluru, India", "unknown")


def test_parse_employment_type():
    assert parse_employment_type("Full-time") == "full_time"
    assert parse_employment_type("FULL TIME") == "full_time"
    assert parse_employment_type("Part-time") == "part_time"
    assert parse_employment_type("Internship") == "internship"
    assert parse_employment_type("Contract") == "contract"
    assert parse_employment_type("Unknown") == "unknown"
    assert parse_employment_type("") == "unknown"


def test_parse_experience_level():
    assert parse_experience_level("Junior Developer", "We need a junior") == "entry"
    assert parse_experience_level("Senior Engineer", "Any level") == "senior"
    assert parse_experience_level("Director", "Lead team") == "senior"
    assert parse_experience_level("Associate", "Mid level") == "mid"
    assert parse_experience_level("Intern", "Summer intern") == "intern"
    assert parse_experience_level("Fresher", "Graduate") == "fresher"
    assert parse_experience_level("Unknown Role", "No experience needed") == "no_experience"
    assert parse_experience_level("Manager", "5+ years experience") == "unknown"


def test_parse_salary():
    # Test basic parsing
    assert parse_salary("$100,000") == (100000.0, 100000.0, "USD")
    assert parse_salary("₹50,000 - ₹60,000") == (50000.0, 60000.0, "INR")
    assert parse_salary("50k EUR") == (50000.0, 50000.0, "EUR")
    assert parse_salary("€40,000 - £50,000") == (40000.0, 50000.0, "EUR")  # prefix wins
    assert parse_salary("100000 USD") == (100000.0, 100000.0, "USD")
    assert parse_salary("80k - 120k USD") == (80000.0, 120000.0, "USD")
    # Ensure min <= max
    assert parse_salary("$120,000 - $100,000") == (100000.0, 120000.0, "USD")
    # Test unknown
    assert parse_salary(None) == (None, None, "unknown")
    assert parse_salary("") == (None, None, "unknown")
    assert parse_salary("Competitive salary") == (None, None, "unknown")


def test_parse_posted_at():
    from datetime import datetime, timezone
    assert parse_posted_at("2026-08-25") == datetime(2026, 8, 25, tzinfo=timezone.utc)
    assert parse_posted_at("2026-08-25T14:30:00Z") == datetime(2026, 8, 25, 14, 30, 0, tzinfo=timezone.utc)
    # Just check that it returns a datetime with timezone (not worrying about exact offset)
    result = parse_posted_at("2026-08-25T14:30:00+05:30")
    assert isinstance(result, datetime)
    assert result.tzinfo is not None
    assert parse_posted_at("invalid") is None
    assert parse_posted_at("") is None