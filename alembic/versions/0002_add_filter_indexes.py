"""add filter indexes

Phase 2 / AUDIT A4. The read paths filter on is_active, source,
employment_type, experience_level, remote and salary_max, and order by
posted_at, but none of those columns were indexed, so /jobs/search and
/jobs/match were full scans.

is_active and posted_at lead a composite index because every read filters
is_active AND orders by posted_at, so one index serves both.

title and location_normalized are deliberately NOT indexed: they are only
matched with a leading-wildcard LIKE, which no B-tree can serve.

Revision ID: 0002_filter_indexes
Revises: 0001_initial
Create Date: 2026-10-17
"""

from __future__ import annotations

from alembic import op

revision: str = "0002_filter_indexes"
down_revision: str | None = "0001_initial"
branch_labels: str | None = None
depends_on: str | None = None

_INDEXES = (
    "ix_jobs_employment_type",
    "ix_jobs_experience_level",
    "ix_jobs_is_active_posted_at",
    "ix_jobs_remote",
    "ix_jobs_salary_max",
    "ix_jobs_source",
)


def upgrade() -> None:
    op.create_index("ix_jobs_source", "jobs", ["source"], unique=False)
    op.create_index("ix_jobs_employment_type", "jobs", ["employment_type"], unique=False)
    op.create_index("ix_jobs_experience_level", "jobs", ["experience_level"], unique=False)
    op.create_index("ix_jobs_remote", "jobs", ["remote"], unique=False)
    op.create_index("ix_jobs_salary_max", "jobs", ["salary_max"], unique=False)
    op.create_index("ix_jobs_is_active_posted_at", "jobs", ["is_active", "posted_at"], unique=False)


def downgrade() -> None:
    for name in reversed(_INDEXES):
        op.drop_index(name, table_name="jobs")