from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.core.config import get_settings

settings = get_settings()

url = settings.resolved_database_url

# Pool tuning only applies to server-backed dialects. SQLite's default pool is
# SingletonThreadPool, which rejects pool_size/max_overflow/pool_recycle outright
# — so passing them unconditionally made `import backend.core.db` raise
# TypeError whenever DATABASE_URL pointed at SQLite. That turned "MySQL is the
# source of truth" into an unimportable module rather than a documented
# restriction, and is why every test needed a file-backed MySQL-shaped URL.
_IS_SQLITE = url.startswith("sqlite")
_pool_kwargs = (
    {}
    if _IS_SQLITE
    else {
        "pool_pre_ping": True,  # without this, a MySQL-closed idle connection
                               # (default wait_timeout=8h) throws OperationalError on
                               # first use after the app has been quiet — pre_ping
                               # tests the connection and transparently reconnects.
        "pool_recycle": 280,     # recycle before typical managed-MySQL idle timeouts
                               # (RDS/PlanetScale proxies often cut ~5min) so we never
                               # hand out a connection the proxy already dropped.
        "pool_size": 10,
        "max_overflow": 20,
    }
)

engine = create_engine(url, future=True, **_pool_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
