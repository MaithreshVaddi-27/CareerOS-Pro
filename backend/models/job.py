"""MySQL is the source of truth (spec 2.5). Qdrant/Redis are not canonical."""

import datetime as dt
import uuid

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Job(Base):
    __tablename__ = "jobs"

    # Every read path filters is_active and orders by posted_at, so they lead a
    # composite index: it serves both the predicate and the sort.
    __table_args__ = (Index("ix_jobs_is_active_posted_at", "is_active", "posted_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)

    # identity / dedup
    # unique=True already builds a UNIQUE index, which a B-tree serves for both
    # the uniqueness check and lookups. `index=True` alongside it asked SQLAlchemy
    # for a second, plain index over the same column — pure write amplification
    # on the hottest insert path in the system, with no query it made faster.
    # (An existing deploy still has the redundant ix_jobs_url_hash; dropping it is
    # a one-line manual ALTER, same class as any other column change. See the
    # "no migration tool" note in AUDIT.md.)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)

    # raw + normalized fields
    source: Mapped[str] = mapped_column(String(64), index=True)  # e.g. "jsearch"
    source_id: Mapped[str] = mapped_column(String(512), index=True)
    title: Mapped[str] = mapped_column(String(512))
    company: Mapped[str] = mapped_column(String(256))
    location_raw: Mapped[str] = mapped_column(String(512), default="")
    location_normalized: Mapped[str] = mapped_column(String(256), default="unknown")
    # Indexed because these are equality filters in build_sql_filters. title and
    # location_normalized are not: they are only ever matched with a leading
    # wildcard, which no B-tree can serve.
    employment_type: Mapped[str] = mapped_column(String(64), default="unknown", index=True)
    experience_level: Mapped[str] = mapped_column(String(32), default="unknown", index=True)  # intern/fresher/no_experience/entry/mid/senior/unknown
    remote: Mapped[str] = mapped_column(String(32), default="unknown", index=True)  # remote/onsite/hybrid/unknown

    description: Mapped[str] = mapped_column(Text, default="")
    skills_required: Mapped[list] = mapped_column(JSON, default=list)

    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    salary_currency: Mapped[str] = mapped_column(String(8), default="unknown")

    apply_url: Mapped[str] = mapped_column(String(1024))
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
