"""Database engine management — PostgreSQL via SQLModel."""

from functools import lru_cache

from sqlmodel import Session, create_engine

from src.config import config


@lru_cache(maxsize=1)
def get_engine():
    """Get SQLAlchemy engine (singleton)."""
    url = config["database"]["url"]
    return create_engine(url, echo=False)


def get_session() -> Session:
    """Get a new SQLModel session."""
    return Session(get_engine())
