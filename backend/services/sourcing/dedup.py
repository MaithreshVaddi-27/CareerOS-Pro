"""Deterministic dedup (spec 2.1). Two-stage: exact url_hash, then content_hash fallback."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Job
from backend.schemas import NormalizedJob


def find_duplicate(db: Session, job: NormalizedJob) -> Job | None:
    """First existing row that matches, by url_hash then content_hash.

    `.first()`, not `scalar_one_or_none()`: content_hash is indexed but NOT
    unique, and two rows can legitimately share one — two different apply_urls
    that normalise to the same title/company/location, inserted by two
    concurrent searches that both missed. `scalar_one_or_none()` then raises
    MultipleResultsFound, and since the duplicate rows are permanent, every
    later search touching that hash 500s forever. `url_hash` IS unique, but
    using the same lookup shape for both keeps this honest.
    """
    existing = db.execute(
        select(Job).where(Job.url_hash == job.url_hash).limit(1)
    ).scalars().first()
    if existing:
        return existing
    existing = db.execute(
        select(Job).where(Job.content_hash == job.content_hash).limit(1)
    ).scalars().first()
    return existing


def upsert_job(db: Session, job: NormalizedJob) -> tuple[Job, bool]:
    """Insert if new, else return existing (no silent overwrite of verified data). Returns (row, created)."""
    dup = find_duplicate(db, job)
    if dup:
        if not dup.is_active:
            dup.is_active = True
            dup.verified = False
            dup.verified_at = None
            db.flush()
        return dup, False

    row = Job(
        url_hash=job.url_hash,
        content_hash=job.content_hash,
        source=job.source,
        source_id=job.source_id,
        title=job.title,
        company=job.company,
        location_raw=job.location_raw,
        location_normalized=job.location_normalized,
        employment_type=job.employment_type,
        experience_level=job.experience_level,
        remote=job.remote,
        description=job.description,
        skills_required=job.skills_required,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        salary_currency=job.salary_currency,
        apply_url=job.apply_url,
        posted_at=job.posted_at,
    )
    db.add(row)
    # flush, not commit. This is called once per posting from a loop, and MySQL
    # defaults to innodb_flush_log_at_trx_commit=1 — an fsync per commit. A
    # 200-posting ingest was 200 fsyncs and ~1000 round-trips inline in the
    # request. flush() assigns the id without writing to disk; the caller commits
    # once for the batch.
    #
    # Callers MUST commit after their loop. See _commit_batch in main.py /
    # graph.py.
    db.flush()
    return row, True


def commit_batch(db: Session) -> None:
    """Commit everything upsert_job flushed for the current batch."""
    db.commit()
