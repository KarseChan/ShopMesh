"""Database engine management — PostgreSQL via SQLModel."""

from functools import lru_cache

from sqlmodel import Session, create_engine

from src.config import config


@lru_cache(maxsize=1)
def get_engine():
    """Get SQLAlchemy engine (singleton).

    pool_pre_ping: test a pooled connection with a lightweight ping before use and
      transparently reconnect if it's dead — avoids 500s from stale connections
      after the DB restarts / drops idle connections / a network blip severs them
      (real symptom seen: psycopg2 "connection abort 10053" on a long-idle pool).
    pool_recycle: proactively recycle connections older than 30 min, below common
      server/proxy idle-timeout cutoffs.
    """
    url = config["database"]["url"]
    return create_engine(url, echo=False, pool_pre_ping=True, pool_recycle=1800)


def get_session() -> Session:
    """Get a new SQLModel session."""
    return Session(get_engine())
