"""SQL and Python filter implementations must agree.

`build_sql_filters` exists so `/jobs/match` stops loading the whole jobs table
(audit A4). It is only safe as a *prefilter* if it never excludes a row that
`passes_hard_filters` would accept — the Python predicate stays authoritative.

These tests run both implementations against a real database and compare. The
models use portable column types, so SQLite is enough to execute the SQL; the
production dialect is MySQL.
"""

import datetime as dt

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from backend.core.db import Base
from backend.models import Job
from backend.schemas import SearchRequest
from backend.services.filters import apply_hard_filters, build_sql_filters

NOW = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.timezone.utc)


def _job(**kw) -> Job:
    ident = kw.pop("id", "0" * 32)
    base = dict(
        id=ident,
        # Unique per row: url_hash carries a UNIQUE constraint.
        url_hash=kw.pop("url_hash", f"{ident}".ljust(64, "u")),
        content_hash=kw.pop("content_hash", f"{ident}".ljust(64, "c")),
        source=kw.pop("source", "jsearch"),
        source_id=kw.pop("source_id", f"s-{ident}"),
        title=kw.pop("title", "Python Engineer"),
        company=kw.pop("company", "Acme"),
        location_raw=kw.pop("location_raw", ""),
        location_normalized=kw.pop("location_normalized", "unknown"),
        employment_type=kw.pop("employment_type", "unknown"),
        experience_level=kw.pop("experience_level", "unknown"),
        remote=kw.pop("remote", "unknown"),
        description=kw.pop("description", ""),
        skills_required=[],
        salary_min=None,
        salary_max=kw.pop("salary_max", None),
        salary_currency="EUR",
        apply_url=kw.pop("apply_url", "https://example.invalid/1"),
        posted_at=kw.pop("posted_at", None),
        is_active=kw.pop("is_active", True),
        verified=False,
        verified_at=None,
        created_at=NOW.replace(tzinfo=None),
        updated_at=NOW.replace(tzinfo=None),
    )
    base.update(kw)
    return Job(**base)


# Deliberately covers every branch that could make the two implementations
# disagree: aliases, remote escape, unknowns, NULLs, and LIKE metacharacters.
FIXTURES = [
    _job(id="01", location_normalized="London, United Kingdom", location_raw="London, UK",
         employment_type="full_time", experience_level="senior", salary_max=120000,
         posted_at=NOW.replace(tzinfo=None) - dt.timedelta(days=2)),
    _job(id="02", location_normalized="New York, NY", location_raw="New York, NY",
         employment_type="full_time", experience_level="mid", remote="hybrid",
         salary_max=None, posted_at=None),
    _job(id="03", location_normalized="Hyderabad, Telangana, India", remote="remote",
         description="100% remote worldwide", posted_at=None, salary_max=None),
    _job(id="04", location_normalized="Berlin, Germany", employment_type="contract",
         experience_level="senior", salary_max=90000,
         posted_at=NOW.replace(tzinfo=None) - dt.timedelta(days=400)),
    _job(id="05", location_normalized="unknown", is_active=False,
         title="Python Engineer", salary_max=50000),
    _job(id="06", location_normalized="Paris, France", title="Rust Engineer",
         description="Systems work", salary_max=150000),
    _job(id="07", location_normalized="unknown", title="100% coverage specialist",
         description="we test everything", employment_type="part_time"),
    _job(id="08", location_normalized="Dubai, United Arab Emirates", remote="remote"),
    _job(id="09", location_normalized="unknown", employment_type="unknown",
         experience_level="unknown", salary_max=None, posted_at=None, source="company"),
]


@pytest.fixture(scope="module")
def session():
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all(FIXTURES)
    db.commit()
    yield db
    db.close()
    engine.dispose()


REQUESTS = [
    SearchRequest(),
    SearchRequest(query="python"),
    SearchRequest(query="Python"),
    SearchRequest(query="rust"),
    SearchRequest(query="100%"),           # LIKE metacharacter in user input
    SearchRequest(query="_"),             # single-char wildcard
    SearchRequest(query="nothingmatches"),
    SearchRequest(location="uk"),          # alias -> "united kingdom"
    SearchRequest(location="usa"),
    SearchRequest(location="us"),
    SearchRequest(location="dubai"),
    SearchRequest(location="london"),      # substring of "London, United Kingdom"
    SearchRequest(location="germany"),
    SearchRequest(location="india"),       # comma-part match
    SearchRequest(location="paris"),
    SearchRequest(location="Berlin"),       # case sensitivity
    SearchRequest(location="atlantis"),
    SearchRequest(location="ny"),          # "New York, NY"
    SearchRequest(employment_type="full_time"),
    SearchRequest(employment_type="unknown"),
    SearchRequest(experience_level="senior"),
    SearchRequest(remote_only=True),
    SearchRequest(min_salary=100000),
    SearchRequest(min_salary=999999),
    SearchRequest(posted_within_days=7),
    SearchRequest(posted_within_days=365),
    SearchRequest(sources=["company"]),
    SearchRequest(query="engineer", location="uk", employment_type="full_time",
                  experience_level="senior", min_salary=100000, posted_within_days=30),
    SearchRequest(query="python", remote_only=True, posted_within_days=1),
]


@pytest.mark.parametrize("req", REQUESTS, ids=lambda r: repr(r.model_dump(exclude_defaults=True))[:70])
def test_sql_prefilter_never_excludes_a_python_match(session, req):
    """The safety property: SQL result must be a superset of the Python result.

    If SQL is narrower, `/jobs/match` silently loses eligible jobs.
    """
    everything = session.execute(sa.select(Job)).scalars().all()
    python_ids = {j.id for j in apply_hard_filters(everything, req)}

    sql_ids = set(session.execute(sa.select(Job).where(*build_sql_filters(req, now=NOW))).scalars().all())
    sql_ids = {j.id for j in sql_ids}

    missing = python_ids - sql_ids
    assert not missing, (
        f"SQL prefilter dropped rows the Python filter accepts: {sorted(missing)}\n"
        f"request: {req.model_dump(exclude_defaults=True)}"
    )


# Location needles where the SQL form is intentionally WIDER than the Python
# predicate. `LIKE '%ny%'` matches "germany", but passes_hard_filters correctly
# requires a word boundary and rejects it. Expressing word boundaries in SQL
# would need dialect-specific string munging (REGEXP is absent in SQLite), which
# is more divergence risk than the extra rows cost — the Python pass narrows
# them right back down.
#
# A new divergence here must be a conscious decision: if a needle is not listed,
# the exactness test fails and the implementer must choose superset-or-fix.
#
# 'ny' hits "germany"; 'in' hits "london, united kingdom" (k-in-gdom) and
# "dubai". Both are the same word-boundary gap, and both are values users
# actually search for, so they are declared rather than avoided.
KNOWN_SQL_OVERMATCH_NEEDLES = {"ny", "in"}


def _may_overmatch(req: SearchRequest) -> bool:
    return bool(req.location) and req.location.strip().lower() in KNOWN_SQL_OVERMATCH_NEEDLES


@pytest.mark.parametrize("req", REQUESTS, ids=lambda r: repr(r.model_dump(exclude_defaults=True))[:70])
def test_sql_and_python_agree_exactly(session, req):
    """Stronger than required: apart from declared over-matches, the two should
    return the same rows. This is what catches a filter implemented on only one
    side (the `sources` filter was missing from the Python predicate)."""
    if _may_overmatch(req):
        pytest.skip(f"declared SQL over-match for location={req.location!r}")

    everything = session.execute(sa.select(Job)).scalars().all()
    python_ids = {j.id for j in apply_hard_filters(everything, req)}
    sql_ids = {j.id for j in session.execute(sa.select(Job).where(*build_sql_filters(req, now=NOW))).scalars().all()}

    assert sql_ids == python_ids, (
        f"divergence\n  extra in SQL:    {sorted(sql_ids - python_ids)}\n"
        f"  missing from SQL: {sorted(python_ids - sql_ids)}\n"
        f"  request: {req.model_dump(exclude_defaults=True)}"
    )


def test_declared_overmatch_is_still_a_superset(session):
    """The declared over-match must stay over-matching, not become narrower."""
    req = SearchRequest(location="ny")
    everything = session.execute(sa.select(Job)).scalars().all()
    python_ids = {j.id for j in apply_hard_filters(everything, req)}
    sql_ids = {j.id for j in session.execute(sa.select(Job).where(*build_sql_filters(req, now=NOW))).scalars().all()}
    assert python_ids <= sql_ids
    assert sql_ids - python_ids, "if this no longer over-matches, drop it from KNOWN_SQL_OVERMATCH_NEEDLES"


def test_location_us_does_not_recurse():
    """Regression: 'us' and 'usa' alias each other, so the matcher recursed until
    the stack blew and the endpoint returned 500 for location=us / location=usa."""
    probes = [
        _job(id="p1", location_normalized="New York, NY", remote="hybrid"),
        _job(id="p2", location_normalized="London, United Kingdom"),
        _job(id="p3", location_normalized="Dubai, United Arab Emirates", remote="remote"),
    ]
    for needle in ("us", "usa", "uk", "uae", "dubai"):
        ids = {j.id for j in apply_hard_filters(probes, SearchRequest(location=needle))}
        assert isinstance(ids, set)


def test_country_codes_match_their_country():
    """Short ISO-style codes must find the country, which a bare substring
    cannot do: 'in' is not a substring of 'hyderabad, telangana, india'."""
    probes = [
        _job(id="i1", location_normalized="Hyderabad, Telangana, India"),
        _job(id="i2", location_normalized="Bengaluru, Karnataka, India"),
        _job(id="i3", location_normalized="London, United Kingdom"),
        _job(id="i4", location_normalized="Paris, France"),
    ]
    for needle, expected in (("in", {"i1", "i2"}), ("india", {"i1", "i2"})):
        ids = {j.id for j in apply_hard_filters(probes, SearchRequest(location=needle))}
        assert ids == expected, f"location={needle!r} matched {sorted(ids)}"


def test_india_agrees_between_sql_and_python(session):
    """India in both spellings, via SQL and via the Python predicate."""
    for needle in ("india", "in", "bengaluru", "telangana"):
        req = SearchRequest(location=needle)
        everything = session.execute(sa.select(Job)).scalars().all()
        python_ids = {j.id for j in apply_hard_filters(everything, req)}
        sql_ids = {j.id for j in session.execute(sa.select(Job).where(*build_sql_filters(req, now=NOW))).scalars().all()}
        # Never narrower than Python, whatever the needle.
        assert python_ids <= sql_ids, f"SQL narrowed location={needle!r}"
        if not _may_overmatch(req):
            assert sql_ids == python_ids, f"divergence for location={needle!r}"


def test_remote_job_matches_any_location_filter(session):
    """The remote escape hatch must survive the SQL translation."""
    everything = session.execute(sa.select(Job)).scalars().all()
    req = SearchRequest(location="atlantis")
    python_ids = {j.id for j in apply_hard_filters(everything, req)}
    sql_ids = {j.id for j in session.execute(sa.select(Job).where(*build_sql_filters(req, now=NOW))).scalars().all()}
    assert "03" in python_ids and "03" in sql_ids


def test_like_metacharacters_are_escaped(session):
    """`query='100%'` must match the literal text, not act as a wildcard."""
    everything = session.execute(sa.select(Job)).scalars().all()
    req = SearchRequest(query="100%")
    python_ids = {j.id for j in apply_hard_filters(everything, req)}
    sql_ids = {j.id for j in session.execute(sa.select(Job).where(*build_sql_filters(req, now=NOW))).scalars().all()}
    assert "07" in python_ids
    assert sql_ids == python_ids


def test_inactive_rows_always_excluded(session):
    everything = session.execute(sa.select(Job)).scalars().all()
    req = SearchRequest()
    sql_ids = {j.id for j in session.execute(sa.select(Job).where(*build_sql_filters(req, now=NOW))).scalars().all()}
    assert "05" not in sql_ids, "is_active=False row leaked through"
    assert "05" not in {j.id for j in apply_hard_filters(everything, req)}