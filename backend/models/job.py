"""MySQL is the source of truth (spec 2.5). Qdrant/Redis are not canonical."""

import datetime as dt
import uuid

from sqlalchemy import JSON, Boolean, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)

    # identity / dedup
    url_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)

    # raw + normalized fields
    source: Mapped[str] = mapped_column(String(64))  # e.g. "jsearch"
    source_id: Mapped[str] = mapped_column(String(512), index=True)
    title: Mapped[str] = mapped_column(String(512))
    company: Mapped[str] = mapped_column(String(256))
    location_raw: Mapped[str] = mapped_column(String(512), default="")
    location_normalized: Mapped[str] = mapped_column(String(256), default="unknown")
    employment_type: Mapped[str] = mapped_column(String(64), default="unknown")
    experience_level: Mapped[str] = mapped_column(String(32), default="unknown")  # intern/fresher/no_experience/entry/mid/senior/unknown
    remote: Mapped[str] = mapped_column(String(32), default="unknown")  # remote/onsite/hybrid/unknown

    description: Mapped[str] = mapped_column(Text, default="")
    skills_required: Mapped[list] = mapped_column(JSON, default=list)

    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str] = mapped_column(String(8), default="unknown")

    apply_url: Mapped[str] = mapped_column(String(1024))
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
