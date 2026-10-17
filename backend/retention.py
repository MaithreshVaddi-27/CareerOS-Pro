"""Bounded retention for inactive jobs (AUDIT A4 / item 6).

When a posting disappears upstream it is flipped to ``is_active = false``
rather than deleted, so the table only ever grows. This module prunes those
rows, under two constraints:

* **Dry run by default.** Deleting jobs is destructive, so a run reports what
  it *would* delete and changes nothing unless ``--yes`` is passed.
* **Bounded.** Each run deletes at most ``batch_size`` rows in one short
  transaction, so a first run against a badly overgrown table cannot hold a
  long lock or produce a huge binlog. Re-run until the count reaches zero.

Which clock to use: ``updated_at`` is bumped by ``onupdate`` whenever the row
is written, and the ``is_active`` flip *is* a write. So ``updated_at`` is when
the job last went inactive, which is the right retention clock here — not
``created_at``, which would expire freshly-refreshed rows on their age rather
than their staleness.

Usage:
    python -m backend.retention --days 90                 # report only
    python -m backend.retention --days 90 --yes           # delete one batch
    python -m backend.retention --days 90 --yes --batch-size 5000
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

import sqlalchemy as sa
from sqlalchemy.orm import Session

from backend.core.db import SessionLocal
from backend.models import Job

# Updated_at is a naive DATETIME holding UTC, so compare against naive UTC.
DEFAULT_DAYS = 90
DEFAULT_BATCH_SIZE = 1000


def count_candidates(
    session: Session, *, days: int, now: dt.datetime | None = None
) -> int:
    """How many rows a real run would delete right now."""
    stmt = sa.select(sa.func.count()).select_from(Job).where(*_where(days=days, now=now))
    return int(session.execute(stmt).scalar_one())


def prune_inactive_jobs(
    session: Session,
    *,
    days: int = DEFAULT_DAYS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    now: dt.datetime | None = None,
    dry_run: bool = True,
) -> int:
    """Delete up to ``batch_size`` inactive jobs untouched for ``days``.

    Returns the number of rows deleted (0 for a dry run).
    """
    if days < 0:
        raise ValueError("days must be >= 0")
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")

    if dry_run:
        return 0

    stmt = (
        sa.select(Job.id)
        .where(*_where(days=days, now=now))
        # Deterministic order so a batched run is reproducible and repeatable.
        # Order before limit, not after.
        .order_by(Job.updated_at, Job.id)
        .limit(batch_size)
    )
    ids = [row[0] for row in session.execute(stmt)]
    if not ids:
        return 0

    session.execute(sa.delete(Job).where(Job.id.in_(ids)))
    session.commit()
    return len(ids)


def _where(*, days: int, now: dt.datetime | None):
    """The single definition of "eligible for deletion".

    Count and delete both go through this, so a report can never disagree with
    the deletion it describes.
    """
    reference = now or dt.datetime.now(dt.timezone.utc)
    # updated_at is a naive DATETIME holding UTC, so compare against naive UTC.
    cutoff = reference.replace(tzinfo=None) - dt.timedelta(days=days)
    return (Job.is_active.is_(False), Job.updated_at < cutoff)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.retention",
        description="Prune inactive jobs. Reports by default; deletes only with --yes.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Delete inactive jobs untouched for at least this many days (default {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Max rows to delete per run (default {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete. Without this flag the run is a dry run.",
    )
    args = parser.parse_args(argv)

    if args.days < 0:
        parser.error("--days must be >= 0")
    if args.batch_size <= 0:
        parser.error("--batch-size must be > 0")

    session = SessionLocal()
    try:
        total = count_candidates(session, days=args.days)
        if not args.yes:
            print(
                f"[dry run] {total} inactive job(s) older than {args.days}d would be deleted."
                "\nRe-run with --yes to delete (in batches of "
                f"{args.batch_size})."
            )
            return 0

        deleted = prune_inactive_jobs(
            session,
            days=args.days,
            batch_size=args.batch_size,
            dry_run=False,
        )
        remaining = total - deleted
        print(f"deleted {deleted} inactive job(s) older than {args.days}d")
        if remaining > 0:
            print(f"{remaining} still eligible — re-run to continue (batches are bounded).")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())