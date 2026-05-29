"""Redis sliding-window rate limiter using Lua scripts.

Three tiers of rate limiting:
- per-user:   individual user request budget
- per-tenant: tenant-wide request budget (protects shared resources)
- global:     system-wide safety cap

Uses a sorted-set sliding window: each request adds a scored member
(now_ms as score, UUID as value). Lua atomically prunes expired entries,
counts survivors, and decides allow/deny in a single Redis round-trip.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import redis.asyncio as aioredis
from redis.exceptions import NoScriptError
import structlog

from src.config import config

logger = structlog.get_logger(__name__)

# ---------- Lua script: sliding window check ----------

# KEYS[1] = sorted-set key
# ARGV[1] = window start (ms), ARGV[2] = now (ms), ARGV[3] = max_requests
# ARGV[4] = TTL (seconds) — auto-expire stale keys
# Returns: {allowed (0/1), current_count, retry_after_ms}
_SLIDING_WINDOW_SCRIPT = """
local key         = KEYS[1]
local window_start = tonumber(ARGV[1])
local now_ms       = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local ttl_sec      = tonumber(ARGV[4])

-- Prune entries outside the window
redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

-- Count remaining entries in the window
local count = redis.call('ZCARD', key)

if count < max_requests then
    -- Allowed: add this request
    redis.call('ZADD', key, now_ms, now_ms .. ':' .. math.random(1000000))
    redis.call('EXPIRE', key, ttl_sec)
    return {1, count + 1, 0}
else
    -- Denied: compute retry_after from the oldest entry
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after = 0
    if #oldest >= 2 then
        retry_after = tonumber(oldest[2]) + (tonumber(ARGV[5]) * 1000) - now_ms
        if retry_after < 0 then retry_after = 0 end
    end
    return {0, count, retry_after}
end
"""

# ---------- Config ----------

def _load_rate_limit_config() -> dict:
    """Load rate limit config from config.yaml."""
    rl_cfg = config.get("rate_limiting", {})
    return {
        "enabled": rl_cfg.get("enabled", True),
        "per_user": {
            "requests": rl_cfg.get("per_user", {}).get("requests", 60),
            "window_seconds": rl_cfg.get("per_user", {}).get("window_seconds", 60),
        },
        "per_tenant": {
            "requests": rl_cfg.get("per_tenant", {}).get("requests", 300),
            "window_seconds": rl_cfg.get("per_tenant", {}).get("window_seconds", 60),
        },
        "global": {
            "requests": rl_cfg.get("global", {}).get("requests", 1000),
            "window_seconds": rl_cfg.get("global", {}).get("window_seconds", 60),
        },
        "exempt_paths": set(rl_cfg.get("exempt_paths", ["/api/health", "/api/auth/login", "/api/auth/register"])),
    }


# ---------- Result ----------

@dataclass
class RateLimitResult:
    allowed: bool
    tier: str           # "per_user" | "per_tenant" | "global" | ""
    current: int
    limit: int
    retry_after_ms: int

    @property
    def retry_after_seconds(self) -> int:
        return max(1, self.retry_after_ms // 1000)


# ---------- Limiter ----------

class RateLimiter:
    """Async Redis sliding-window rate limiter with three tiers."""

    def __init__(self, redis_url: str | None = None):
        self._cfg = _load_rate_limit_config()
        self._redis_url = redis_url or config.get("redis", {}).get("url", "redis://localhost:6379")
        self._redis: aioredis.Redis | None = None
        self._script_sha: str | None = None

    async def _ensure_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            # Register the Lua script
            self._script_sha = await self._redis.script_load(_SLIDING_WINDOW_SCRIPT)
        return self._redis

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    @property
    def enabled(self) -> bool:
        return self._cfg["enabled"]

    @property
    def exempt_paths(self) -> set[str]:
        return self._cfg["exempt_paths"]

    async def check(
        self,
        user_id: str = "",
        tenant_id: str = "",
        path: str = "",
    ) -> RateLimitResult:
        """Check all three rate limit tiers. Returns the first denied result, or allowed."""
        if not self.enabled:
            return RateLimitResult(allowed=True, tier="", current=0, limit=0, retry_after_ms=0)

        # Skip exempt paths
        if path in self._cfg["exempt_paths"]:
            return RateLimitResult(allowed=True, tier="", current=0, limit=0, retry_after_ms=0)

        # Check tiers in order: per_user → per_tenant → global
        tiers = []
        if user_id:
            tiers.append(("per_user", f"rl:user:{user_id}", self._cfg["per_user"]))
        if tenant_id:
            tiers.append(("per_tenant", f"rl:tenant:{tenant_id}", self._cfg["per_tenant"]))
        tiers.append(("global", "rl:global", self._cfg["global"]))

        for tier_name, key, tier_cfg in tiers:
            result = await self._check_tier(key, tier_cfg)
            if not result.allowed:
                result.tier = tier_name
                logger.warning(
                    "rate_limit_exceeded",
                    tier=tier_name,
                    key=key,
                    current=result.current,
                    limit=result.limit,
                )
                return result

        # All tiers passed
        return RateLimitResult(
            allowed=True,
            tier="",
            current=0,
            limit=0,
            retry_after_ms=0,
        )

    async def _check_tier(self, key: str, tier_cfg: dict) -> RateLimitResult:
        """Check a single tier via Lua script."""
        r = await self._ensure_redis()
        now_ms = int(time.time() * 1000)
        window_ms = tier_cfg["window_seconds"] * 1000
        window_start = now_ms - window_ms
        max_requests = tier_cfg["requests"]
        ttl = tier_cfg["window_seconds"] * 2  # generous TTL

        try:
            raw = await r.evalsha(
                self._script_sha,
                1,  # number of keys
                key,
                str(window_start),
                str(now_ms),
                str(max_requests),
                str(ttl),
                str(tier_cfg["window_seconds"]),
            )
            allowed = bool(raw[0])
            current = int(raw[1])
            retry_after_ms = int(raw[2])

            return RateLimitResult(
                allowed=allowed,
                tier="",
                current=current,
                limit=max_requests,
                retry_after_ms=retry_after_ms,
            )
        except NoScriptError:
            # Script evicted from cache — reload and retry
            self._script_sha = await r.script_load(_SLIDING_WINDOW_SCRIPT)
            return await self._check_tier(key, tier_cfg)
        except Exception as e:
            # Redis down — fail open (allow request)
            logger.error("rate_limit_redis_error", error=str(e))
            return RateLimitResult(
                allowed=True,
                tier="",
                current=0,
                limit=max_requests,
                retry_after_ms=0,
            )
