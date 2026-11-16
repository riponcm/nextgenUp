"""SQLAlchemy database engine, session factory, and lifecycle management."""

from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from demerivo.config import Settings, get_settings


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy 2.0 ORM models."""
    pass


def create_db_engine(database_url: str):
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(
        database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )


# Global engine and session factory initialized lazily
_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_db_engine(settings.database_url)
    return _engine


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=get_engine(),
            expire_on_commit=False,
        )
    return _session_factory


def SessionLocal() -> Session:
    """Instantiate a new database session."""
    factory = get_session_factory()
    return factory()


def init_db(engine=None):
    """Create all database tables."""
    eng = engine or get_engine()
    Base.metadata.create_all(bind=eng)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
