"""Synchronous SQLAlchemy engine and session factory."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import DB_DSN_SYNC

engine = create_engine(
    DB_DSN_SYNC,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db():
    """Yield a database session (context manager or FastAPI dependency)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables if they don't exist (idempotent)."""
    from storage.models import Base
    Base.metadata.create_all(bind=engine)
