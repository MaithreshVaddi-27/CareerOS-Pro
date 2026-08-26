"""Deterministic dedup (spec 2.1). Two-stage: exact url_hash, then content_hash fallback."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Job
from backend.schemas import NormalizedJob


def find_duplicate(db: Session, job: NormalizedJob) -> Job | None:
    existing = db.execute(select(Job).where(Job.url_hash == job.url_hash)).scalar_one_or_none()
    if existing:
        return existing
    existing = db.execute(select(Job).where(Job.content_hash == job.content_hash)).scalar_one_or_none()
    return existing


def upsert_job(db: Session, job: NormalizedJob) -> tuple[Job, bool]:
    """Insert if new, else return existing (no silent overwrite of verified data). Returns (row, created)."""
    dup = find_duplicate(db, job)
    if dup:
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
    db.commit()
    db.refresh(row)
    return row, True
