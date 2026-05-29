"""Tests for Redis distributed rate limiter (P3-T1).

Tests use fakeredis for isolation — no real Redis required.
"""

import pytest
import time
from unittest.mock import patch, AsyncMock

from src.ratelimit.limiter import RateLimiter, RateLimitResult, _load_rate_limit_config


# ---------- Config tests ----------

class TestRateLimitConfig:
    """Test config loading from config.yaml."""

    def test_default_config_structure(self):
        cfg = _load_rate_limit_config()
        assert "enabled" in cfg
        assert "per_user" in cfg
        assert "per_tenant" in cfg
        assert "global" in cfg
        assert "exempt_paths" in cfg

    def test_default_values(self):
        cfg = _load_rate_limit_config()
        assert cfg["per_user"]["requests"] == 60
        assert cfg["per_user"]["window_seconds"] == 60
        assert cfg["per_tenant"]["requests"] == 300
        assert cfg["global"]["requests"] == 1000

    def test_exempt_paths(self):
        cfg = _load_rate_limit_config()
        assert "/api/health" in cfg["exempt_paths"]
        assert "/api/auth/login" in cfg["exempt_paths"]
        assert "/api/auth/register" in cfg["exempt_paths"]


# ---------- RateLimitResult tests ----------

class TestRateLimitResult:
    def test_retry_after_seconds_minimum(self):
        r = RateLimitResult(allowed=False, tier="per_user", current=10, limit=10, retry_after_ms=500)
        assert r.retry_after_seconds == 1  # min 1 second

    def test_retry_after_seconds_normal(self):
        r = RateLimitResult(allowed=False, tier="per_user", current=10, limit=10, retry_after_ms=3000)
        assert r.retry_after_seconds == 3


# ---------- Limiter unit tests ----------

class TestRateLimiter:
    """Test the RateLimiter with mocked Redis."""

    @pytest.fixture
    def limiter(self):
        """Create a limiter with mocked Redis."""
        with patch("src.ratelimit.limiter.config") as mock_config:
            mock_config.get.return_value = {
                "enabled": True,
                "per_user": {"requests": 3, "window_seconds": 60},
                "per_tenant": {"requests": 10, "window_seconds": 60},
                "global": {"requests": 100, "window_seconds": 60},
                "exempt_paths": ["/api/health", "/api/auth/login"],
            }
            limiter = RateLimiter()
            limiter._cfg = {
                "enabled": True,
                "per_user": {"requests": 3, "window_seconds": 60},
                "per_tenant": {"requests": 10, "window_seconds": 60},
                "global": {"requests": 100, "window_seconds": 60},
                "exempt_paths": {"/api/health", "/api/auth/login"},
            }
            yield limiter

    def test_exempt_path_bypasses(self, limiter):
        """Exempt paths should always be allowed."""
        assert "/api/health" in limiter.exempt_paths
        # Even without Redis, exempt paths return allowed
        import asyncio
        result = asyncio.run(limiter.check(path="/api/health"))
        assert result.allowed is True

    def test_disabled_limiter_allows_all(self, limiter):
        """Disabled limiter should allow all requests."""
        limiter._cfg["enabled"] = False
        import asyncio
        result = asyncio.run(limiter.check(user_id="u1", tenant_id="t1", path="/api/chat"))
        assert result.allowed is True
        limiter._cfg["enabled"] = True  # restore

    def test_check_calls_tiers(self, limiter):
        """Check should call per_user, per_tenant, and global tiers."""
        import asyncio

        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="sha123")
        # Mock evalsha to always return allowed
        mock_redis.evalsha = AsyncMock(return_value=[1, 1, 0])

        limiter._redis = mock_redis
        limiter._script_sha = "sha123"

        result = asyncio.run(limiter.check(user_id="u1", tenant_id="t1", path="/api/chat"))
        assert result.allowed is True
        # evalsha should be called 3 times (per_user, per_tenant, global)
        assert mock_redis.evalsha.call_count == 3

    def test_check_denies_at_per_user_tier(self, limiter):
        """Should deny at per_user tier when limit exceeded."""
        import asyncio

        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="sha123")
        # First call (per_user) returns denied
        # Second call (per_tenant) returns allowed
        mock_redis.evalsha = AsyncMock(side_effect=[
            [0, 3, 5000],   # per_user: denied, 3 current, 5s retry
            [1, 1, 0],      # per_tenant: allowed
            [1, 1, 0],      # global: allowed
        ])

        limiter._redis = mock_redis
        limiter._script_sha = "sha123"

        result = asyncio.run(limiter.check(user_id="u1", tenant_id="t1", path="/api/chat"))
        assert result.allowed is False
        assert result.tier == "per_user"
        assert result.retry_after_ms == 5000

    def test_check_denies_at_global_tier(self, limiter):
        """Should deny at global tier when limit exceeded."""
        import asyncio

        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="sha123")
        mock_redis.evalsha = AsyncMock(side_effect=[
            [1, 1, 0],      # per_user: allowed
            [1, 1, 0],      # per_tenant: allowed
            [0, 100, 3000],  # global: denied
        ])

        limiter._redis = mock_redis
        limiter._script_sha = "sha123"

        result = asyncio.run(limiter.check(user_id="u1", tenant_id="t1", path="/api/chat"))
        assert result.allowed is False
        assert result.tier == "global"

    def test_redis_error_fails_open(self, limiter):
        """When Redis is down, fail open (allow request)."""
        import asyncio

        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="sha123")
        mock_redis.evalsha = AsyncMock(side_effect=Exception("Connection refused"))

        limiter._redis = mock_redis
        limiter._script_sha = "sha123"

        result = asyncio.run(limiter.check(user_id="u1", tenant_id="t1", path="/api/chat"))
        assert result.allowed is True  # fail open

    def test_no_user_id_skips_user_tier(self, limiter):
        """Without user_id, per_user tier should be skipped."""
        import asyncio

        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="sha123")
        mock_redis.evalsha = AsyncMock(return_value=[1, 1, 0])

        limiter._redis = mock_redis
        limiter._script_sha = "sha123"

        result = asyncio.run(limiter.check(user_id="", tenant_id="t1", path="/api/chat"))
        assert result.allowed is True
        # Only per_tenant + global = 2 calls
        assert mock_redis.evalsha.call_count == 2


# ---------- Integration test (requires Redis) ----------

@pytest.mark.asyncio
class TestRateLimiterIntegration:
    """Integration tests — skipped if Redis is unavailable."""

    @pytest.fixture
    async def limiter(self):
        """Create a real limiter connected to Redis."""
        with patch("src.ratelimit.limiter.config") as mock_config:
            mock_config.get.return_value = {
                "enabled": True,
                "per_user": {"requests": 3, "window_seconds": 10},
                "per_tenant": {"requests": 10, "window_seconds": 10},
                "global": {"requests": 100, "window_seconds": 10},
                "exempt_paths": {"/api/health"},
            }
            limiter = RateLimiter()
            limiter._cfg = mock_config.get.return_value
            try:
                r = await limiter._ensure_redis()
                await r.ping()
            except Exception:
                pytest.skip("Redis not available")
            yield limiter
            # Cleanup: delete test keys
            r = limiter._redis
            if r:
                keys = await r.keys("rl:*")
                if keys:
                    await r.delete(*keys)
                await limiter.close()

    async def test_allows_under_limit(self, limiter):
        """Requests under the limit should be allowed."""
        for _ in range(3):
            result = await limiter.check(user_id="test_user", tenant_id="test_tenant", path="/api/chat")
            assert result.allowed is True

    async def test_denies_over_limit(self, limiter):
        """Requests over the limit should be denied."""
        # Exhaust the per-user limit (3 requests)
        for _ in range(3):
            await limiter.check(user_id="limit_user", tenant_id="limit_tenant", path="/api/chat")

        # 4th request should be denied
        result = await limiter.check(user_id="limit_user", tenant_id="limit_tenant", path="/api/chat")
        assert result.allowed is False
        assert result.tier == "per_user"
        assert result.retry_after_ms > 0

    async def test_different_users_independent(self, limiter):
        """Rate limits for different users are independent."""
        # Exhaust limit for user A
        for _ in range(3):
            await limiter.check(user_id="user_a", tenant_id="shared_tenant", path="/api/chat")

        # User B should still be allowed
        result = await limiter.check(user_id="user_b", tenant_id="shared_tenant", path="/api/chat")
        assert result.allowed is True
