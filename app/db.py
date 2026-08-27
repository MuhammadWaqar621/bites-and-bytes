"""Database engine and session plumbing.

One SQLAlchemy engine for the whole process. The URL comes from `.env`, so the
same code runs against SQL Server (what this project ships with), PostgreSQL,
or an in-memory SQLite database in the tests -- nothing above this layer knows
or cares which.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base every ORM model inherits from."""


_settings = get_settings()

#: Driver-specific options. `fast_executemany` turns the seeder's 36,000
#: inserts from minutes into seconds, but it exists only on the SQL Server
#: dialect -- passing it to SQLite is a TypeError, not a no-op.
_engine_options: dict = {}
if _settings.database_url.startswith("mssql"):
    _engine_options["fast_executemany"] = True

# `pool_pre_ping` costs one cheap round-trip per checkout and saves you from
# the "server closed the connection unexpectedly" error after an idle spell --
# which SQL Server Express, with its aggressive auto-close, will hand you.
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    future=True,
    **_engine_options,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                           future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional block for scripts (the seeder) and background work."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
