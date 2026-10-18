"""Deterministic normalization. No LLM here (spec 2.1). Unknown stays unknown (2.3)."""

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from backend.schemas import NormalizedJob, RawJobPosting

_REMOTE_WORDS = ("remote", "work from home", "wfh")
_HYBRID_WORDS = ("hybrid",)
_ONSITE_WORDS = ("on-site", "onsite", "in-office", "in office")

_EMPLOYMENT_MAP = {
    "full-time": "full_time",
    "full time": "full_time",
    "fulltime": "full_time",
    "part-time": "part_time",
    "part time": "part_time",
    "internship": "internship",
    "intern": "internship",
    "contract-to-hire": "contract",
    "contract to hire": "contract",
    "contract": "contract",
    "contractor": "contract",
    "temporary": "temporary",
    "temp": "temporary",
}

# order matters — checked top to bottom, first match wins
_EXPERIENCE_MARKERS = [
    ("intern", "intern"),
    ("internship", "intern"),
    ("freshers welcome", "fresher"),
    ("fresher", "fresher"),
    ("no prior experience", "no_experience"),
    ("no experience required", "no_experience"),
    ("no experience necessary", "no_experience"),
    ("no experience", "no_experience"),
    ("0 years of experience", "no_experience"),
    ("0 years experience", "no_experience"),
    ("entry level", "entry"),
    ("entry-level", "entry"),
    ("0-1 year", "entry"),
    ("graduate", "entry"),
    ("junior", "entry"),
    ("associate", "mid"),
    ("mid level", "mid"),
    ("mid-level", "mid"),
    ("senior", "senior"),
    ("sr.", "senior"),
    ("lead", "senior"),
    ("principal", "senior"),
    ("staff engineer", "senior"),
    ("director", "senior"),
    ("head of", "senior"),
]

_SALARY_RE = re.compile(
    r"(?P<prefix_currency>₹|\$|€|£|Rs\.?|INR|USD|EUR|GBP)?\s*"
    r"(?P<min>[\d,]+(?:\.\d+)?)\s*(?P<min_k>k|K)?"
    r"(?:\s*[-–to]+\s*"
    r"(?:(?P<mid_currency>₹|\$|€|£|Rs\.?|INR|USD|EUR|GBP)?\s*)?"
    r"(?P<max>[\d,]+(?:\.\d+)?)\s*(?P<max_k>k|K)?"
    r")?"
    r"(?:\s*(?P<postfix_currency>Rs\.?|INR|USD|EUR|GBP))?"
)

_CURRENCY_MAP = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR",
    "$": "USD", "usd": "USD",
    "€": "EUR", "eur": "EUR",
    "£": "GBP", "gbp": "GBP",
}


def normalize_url(raw_url: str) -> str:
    """Strip tracking params/fragment, lowercase scheme+host, for stable dedup hashing."""
    parts = urlsplit(raw_url.strip())
    scheme = parts.scheme.lower() or "https"
    # Strip userinfo (user:pass@) from netloc if present
    netloc = parts.netloc.lower()
    if "@" in netloc:
        netloc = netloc.split("@", 1)[1]
    path = parts.path.rstrip("/")
    # drop query + fragment entirely — most job boards use them for tracking
    return urlunsplit((scheme, netloc, path, "", ""))


def hash_url(raw_url: str) -> str:
    return hashlib.sha256(normalize_url(raw_url).encode("utf-8")).hexdigest()


def hash_content(title: str, company: str, location: str) -> str:
    key = f"{title.strip().lower()}|{company.strip().lower()}|{location.strip().lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def parse_location(location_raw: str) -> tuple[str, str]:
    """Returns (location_normalized, remote_status). Deterministic keyword match only."""
    if not location_raw:
        return "unknown", "unknown"
    low = location_raw.lower()
    if any(w in low for w in _REMOTE_WORDS):
        remote = "remote"
    elif any(w in low for w in _HYBRID_WORDS):
        remote = "hybrid"
    elif any(w in low for w in _ONSITE_WORDS):
        remote = "onsite"
    else:
        remote = "unknown"
    # Collapse whitespace only. Do NOT .title() the value: it destroys
    # acronyms and proper nouns ("NY" -> "Ny", "AWS" -> "Aws"). Location
    # matching in filters.py lowercases both sides, so preserving the source
    # casing costs nothing and keeps the stored value honest (spec 2.3).
    normalized = re.sub(r"\s+", " ", location_raw).strip() or "unknown"
    return normalized, remote


def parse_employment_type(raw: str) -> str:
    if not raw:
        return "unknown"
    low = raw.strip().lower()
    return _EMPLOYMENT_MAP.get(low, "unknown")


def parse_experience_level(title: str, description: str) -> str:
    """Deterministic keyword match against title first (higher signal), then
    description. 'unknown' when nothing matches — never guessed (spec 2.3)."""
    title_low = title.lower()
    for marker, level in _EXPERIENCE_MARKERS:
        if marker in title_low:
            return level
    desc_low = description.lower()
    for marker, level in _EXPERIENCE_MARKERS:
        if marker in desc_low:
            return level
    return "unknown"


def parse_salary(raw: str | None) -> tuple[float | None, float | None, str]:
    """Best-effort deterministic salary parse. Returns (min, max, currency).

    Supports both prefix currency (e.g., "$100,000") and postfix currency
    (e.g., "100,000 USD", "80k EUR", "50000 INR").
    """
    if not raw:
        return None, None, "unknown"
    m = _SALARY_RE.search(raw)
    if not m or not m.group("min"):
        return None, None, "unknown"

    def to_float(s: str | None) -> float | None:
        if not s:
            return None
        return float(s.replace(",", ""))

    lo = to_float(m.group("min"))
    if m.group("min_k"):
        lo *= 1000
    hi_raw = to_float(m.group("max"))
    hi = hi_raw if hi_raw is not None else lo
    if m.group("max_k"):
        hi *= 1000
    # Ensure min <= max
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    # Determine currency: prefer prefix, then mid-range, then postfix
    currency_raw = (
        (m.group("prefix_currency") or "")
        or (m.group("mid_currency") or "")
        or (m.group("postfix_currency") or "")
    ).strip().lower()
    currency = _CURRENCY_MAP.get(currency_raw, "unknown")
    return lo, hi, currency


def parse_posted_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dtobj = datetime.strptime(raw, fmt)
            if dtobj.tzinfo is None:
                dtobj = dtobj.replace(tzinfo=timezone.utc)
            return dtobj
        except ValueError:
            continue
    return None


_SOURCE_ID_MAX = 512


def safe_source_id(source_id: str) -> str:
    """Cap to DB column width. Long IDs (e.g. JSearch base64 blobs) get hashed
    to a fixed-length digest instead of truncated, so uniqueness is kept."""
    if len(source_id) <= _SOURCE_ID_MAX:
        return source_id
    return "sha256:" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()


def normalize_job(raw: RawJobPosting) -> NormalizedJob:
    location_normalized, remote = parse_location(raw.location_raw)
    salary_min, salary_max, currency = parse_salary(raw.salary_raw)

    return NormalizedJob(
        source=raw.source,
        source_id=safe_source_id(raw.source_id),
        title=raw.title.strip(),
        company=raw.company.strip(),
        location_raw=raw.location_raw,
        location_normalized=location_normalized,
        employment_type=parse_employment_type(raw.employment_type_raw),
        experience_level=parse_experience_level(raw.title, raw.description),
        remote=remote,
        description=raw.description,
        skills_required=[],  # filled by skill-extraction step (LLM/ambiguity layer), not here
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=currency,
        apply_url=normalize_url(raw.apply_url),
        posted_at=parse_posted_at(raw.posted_at_raw),
        url_hash=hash_url(raw.apply_url),
        content_hash=hash_content(raw.title, raw.company, raw.location_raw),
    )
