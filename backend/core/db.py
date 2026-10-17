from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.core.config import get_settings

settings = get_settings()

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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
