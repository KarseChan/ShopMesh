"""Redis async client — singleton connection pool."""

import redis.asyncio as aioredis

from src.config import config

_pool: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Get the singleton Redis connection pool."""
    global _pool
    if _pool is None:
        url = config["redis"]["url"]
        _pool = aioredis.from_url(url, decode_responses=True)
    return _pool
