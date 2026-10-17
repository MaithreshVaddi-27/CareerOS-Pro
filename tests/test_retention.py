"""Retention must be boring: dry by default, bounded, and never touch active rows."""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from backend.core.db import Base
from backend.models import Job
from backend.retention import count_candidates, prune_inactive_jobs

NOW = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.timezone.utc)


def _naive(d: dt.datetime) -> dt.datetime:
    return d.replace(tzinfo=None)


@pytest.fixture
def session():
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    rows = []
    # (id, is_active, age_in_days) — ages are relative to NOW.
    for i, (active, age) in enumerate(
        [
            (False, 200),  # stale + inactive -> eligible
            (False, 120),  # eligible
            (False, 91),  # just inside the 90d window -> eligible
            (False, 89),  # too fresh -> keep
            (False, 0),  # just deactivated -> keep
            (True, 500),  # ancient but ACTIVE -> must never be deleted
            (True, 365),
        ]
    ):
        ident = f"{i:032d}"
        rows.append(
            Job(
                id=ident,
                url_hash=f"{ident}".ljust(64, "u"),
                content_hash=f"{ident}".ljust(64, "c"),
                source="jsearch",
                source_id=f"s-{ident}",
                title="Python Engineer",
                company="Acme",
                location_raw="Bengaluru, India",
                location_normalized="Bengaluru, India",
                employment_type="full_time",
                experience_level="mid",
                remote="hybrid",
                description="",
                skills_required=[],
                salary_currency="INR",
                apply_url="https://example.invalid/x",
                posted_at=_naive(NOW),
                is_active=active,
                created_at=_naive(NOW - dt.timedelta(days=age)),
                updated_at=_naive(NOW - dt.timedelta(days=age)),
            )
        )
    db.add_all(rows)
    db.commit()
    yield db
    db.close()


def _ids(db) -> set[str]:
    return {j.id for j in db.execute(sa.select(Job)).scalars()}


def _id(i: int) -> str:
    """Fixture id for row i — matches the zero-padded ids the fixture writes."""
    return f"{i:032d}"


def test_count_respects_the_age_window(session):
    assert count_candidates(session, days=90, now=NOW) == 3


def test_dry_run_changes_nothing(session):
    before = _ids(session)
    assert prune_inactive_jobs(session, days=90, now=NOW, dry_run=True) == 0
    assert _ids(session) == before


def test_prune_removes_only_stale_inactive_rows(session):
    deleted = prune_inactive_jobs(session, days=90, now=NOW, dry_run=False)
    assert deleted == 3
    remaining = {j.id for j in session.execute(sa.select(Job)).scalars()}
    # Rows 0,1,2 are inactive and older than 90d -> deleted.
    assert not ({_id(0), _id(1), _id(2)} & remaining)
    # Rows 3,4 are inactive but inside the window -> kept.
    assert {_id(3), _id(4)} <= remaining
    # Rows 5,6 are ancient but still ACTIVE -> must never be deleted.
    assert {_id(5), _id(6)} <= remaining


def test_prune_is_bounded_by_batch_size(session):
    deleted = prune_inactive_jobs(session, days=90, batch_size=1, now=NOW, dry_run=False)
    assert deleted == 1
    # The rest are still eligible for a subsequent batch.
    assert count_candidates(session, days=90, now=NOW) == 2


def test_repeated_batches_converge(session):
    total = 0
    for _ in range(10):
        total += prune_inactive_jobs(session, days=90, batch_size=2, now=NOW, dry_run=False)
        if count_candidates(session, days=90, now=NOW) == 0:
            break
    assert total == 3
    assert count_candidates(session, days=90, now=NOW) == 0


def test_invalid_arguments_are_rejected(session):
    with pytest.raises(ValueError):
        prune_inactive_jobs(session, days=-1, now=NOW, dry_run=False)
    with pytest.raises(ValueError):
        prune_inactive_jobs(session, days=90, batch_size=0, now=NOW, dry_run=False)


def test_cli_defaults_to_a_dry_run(session, monkeypatch):
    """A bare invocation must not delete anything."""
    from backend import retention

    monkeypatch.setattr(retention, "SessionLocal", lambda: session)
    assert retention.main(["--days", "90"]) == 0
    assert count_candidates(session, days=90, now=NOW) == 3


def test_cli_yes_deletes_one_bounded_batch(session, monkeypatch):
    from backend import retention

    monkeypatch.setattr(retention, "SessionLocal", lambda: session)
    assert retention.main(["--days", "90", "--yes", "--batch-size", "2"]) == 0
    assert count_candidates(session, days=90, now=NOW) == 1