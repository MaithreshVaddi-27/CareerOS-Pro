"""Alembic is the only schema authority.

`alembic check` compares the migrated schema against the SQLAlchemy models and
fails if they differ. Running it in CI is what stops model/migration drift from
re-accumulating — the exact failure mode that produced the original
"Data too long for column source_id" incident (audit A1).

Uses a throwaway SQLite file: the models are portable (String/Text/JSON/Float/
Boolean/DateTime), so autogenerate's comparison is meaningful and this needs no
MySQL in CI.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Resolved_database_url requires these even when targeting SQLite via -x db_url.
ENV = {
    "MYSQL_PASSWORD": "test",
    "API_KEY": "test",
    "PYTHONPATH": str(ROOT),
}


def alembic(*args, db_url):
    import os

    env = {**os.environ, **ENV}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db_url={db_url}", *args],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'test.db'}"


def test_upgrade_creates_the_jobs_table(db_url):
    r = alembic("upgrade", "head", db_url=db_url)
    assert r.returncode == 0, r.stderr

    import sqlalchemy as sa

    engine = sa.create_engine(db_url)
    insp = sa.inspect(engine)
    assert "jobs" in insp.get_table_names()
    assert "alembic_version" in insp.get_table_names()


def test_migrations_match_the_models(db_url):
    """The guard that matters: models and migrations must not diverge."""
    assert alembic("upgrade", "head", db_url=db_url).returncode == 0

    r = alembic("check", db_url=db_url)
    assert r.returncode == 0, (
        "schema drift detected — models changed without a migration.\n"
        "Create one:  alembic revision --autogenerate -m \"describe the change\"\n"
        f"stderr:\n{r.stderr}"
    )


def test_downgrade_removes_everything(db_url):
    assert alembic("upgrade", "head", db_url=db_url).returncode == 0
    assert alembic("downgrade", "base", db_url=db_url).returncode == 0

    import sqlalchemy as sa

    insp = sa.inspect(sa.create_engine(db_url))
    assert "jobs" not in insp.get_table_names()


def test_upgrade_is_idempotent(db_url):
    """Deploys re-run migrations; running twice must be a no-op."""
    assert alembic("upgrade", "head", db_url=db_url).returncode == 0
    assert alembic("upgrade", "head", db_url=db_url).returncode == 0


def test_app_no_longer_runs_startup_ddl():
    """main.py must not create tables at startup — that was the second source of
    truth competing with Alembic. Match the call, not the word: the comment
    explaining *why* it was removed legitimately mentions create_all."""
    src = (ROOT / "backend" / "main.py").read_text()
    assert "metadata.create_all(" not in src
    assert "sync_schema(" not in src


def test_sync_schema_helper_is_gone():
    db = (ROOT / "backend" / "core" / "db.py").read_text()
    assert "def sync_schema" not in db


def test_env_example_matches_settings():
    """Every Settings field must appear in .env.example, spelled exactly right.

    pydantic-settings silently ignores an unknown env var, so a typo like
    RATE_LIMIT_PER_minUTE leaves the default in place with no error anywhere —
    the operator believes they configured something they did not.
    """
    import re

    from backend.core.config import Settings

    fields = {name.upper() for name in Settings.model_fields}
    declared = set()
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Z][A-Z0-9_]+)=", line)
        if m:
            declared.add(m.group(1))

    frontend_only = {v for v in declared if v.startswith("VITE_")}
    declared -= frontend_only  # VITE_* belongs to frontend/.env

    assert not (declared - fields), (
        f".env.example documents vars that do not exist: {sorted(declared - fields)}"
    )
    assert not (fields - declared), (
        f"Settings fields absent from .env.example: {sorted(fields - declared)}"
    )
    assert not (frontend_only - {"VITE_API_BASE"}), (
        f"unknown VITE_ var in .env.example: {sorted(frontend_only)}"
    )