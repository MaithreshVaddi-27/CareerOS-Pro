import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.core.config import get_settings

settings = get_settings()
logger = logging.getLogger("careeros")

url = settings.resolved_database_url
engine = create_engine(
    url,
    future=True,
    pool_pre_ping=True,   # without this, a MySQL-closed idle connection (default
                           # wait_timeout=8h) throws OperationalError on first use
                           # after the app's been quiet a while — pre_ping tests
                           # the connection and transparently reconnects instead.
    pool_recycle=280,      # recycle before typical managed-MySQL idle timeouts
                           # (RDS/PlanetScale proxies often cut ~5min) so we
                           # never hand out a connection the proxy already dropped.
    pool_size=10,
    max_overflow=20,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


def sync_schema(bind=None):
    """create_all() only creates missing tables — it never widens an
    existing column when a model's String() length grows. That drift is
    what caused 'Data too long for column source_id': the table was made
    early on with a shorter VARCHAR and never got ALTERed. Bring String
    columns up to the model's current length on every startup."""
    conn = bind or engine
    insp = inspect(conn)
    if "jobs" not in insp.get_table_names():
        return
    existing = {c["name"]: c for c in insp.get_columns("jobs")}
    from backend.models import Job  # local import: avoid circular import at module load

    with conn.begin() as txn:
        for col in Job.__table__.columns:
            target_len = getattr(col.type, "length", None)
            if target_len is None:
                continue
            current = existing.get(col.name)
            if current is None:
                continue
            current_len = getattr(current["type"], "length", None)
            if current_len is not None and current_len < target_len:
                logger.info(
                    "widening jobs.%s VARCHAR(%s) -> VARCHAR(%s)",
                    col.name, current_len, target_len,
                )
                txn.execute(text(
                    f"ALTER TABLE jobs MODIFY COLUMN `{col.name}` VARCHAR({target_len})"
                ))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
