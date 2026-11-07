"""Database connection layer: one engine per process, short-lived sessions per unit of work."""
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database.models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    """Create the engine once and reuse it (connection pooling instead of a new engine per query)."""
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine

    settings = get_settings()
    url = make_url(settings.database_url)
    is_sqlite = url.get_backend_name() == "sqlite"

    kwargs: dict = {"echo": settings.sql_echo, "pool_pre_ping": True}
    if is_sqlite:
        # FastAPI serves requests from a thread pool; SQLite must allow cross-thread use.
        kwargs["connect_args"] = {"check_same_thread": False}
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, **kwargs)

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")   # SQLite ignores FKs unless told otherwise
            cursor.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    _engine = engine
    _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success, roll back on any error, always close."""
    get_engine()
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create any missing tables. Safe to call on every startup."""
    Base.metadata.create_all(get_engine())


def check_db() -> bool:
    """Return True if the database answers a trivial query."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def reset_engine() -> None:
    """Dispose the pool and forget the engine (used by tests and after changing DATABASE_URL)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
