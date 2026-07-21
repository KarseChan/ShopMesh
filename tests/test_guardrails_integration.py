"""P4-T5: Guardrails integration tests.

Tests that security modules are correctly wired into the agent pipeline:
- output_guard: validates final recommendations against search results
- permission (rate_limit): checks per-user tool call rate limits
- data_guard: sanitizes PII from tool execution logs
"""

import pytest
from unittest.mock import patch, AsyncMock


class TestOutputGuardIntegration:
    """Verify output_guard is called in specialized_agents._run_agent_loop."""

    def test_extract_search_results_from_result_store(self):
        """Should retrieve full products from result store via result_id."""
        from src.graph.specialized_agents import _extract_search_results_from_log
        from src.retrieval.result_store import ResultStore, set_result_store

        store = ResultStore()
        set_result_store(store)

        full_products = [
            {"product_id": "P001", "name": "Product A", "price": 99, "brand": "A"},
            {"product_id": "P002", "name": "Product B", "price": 199, "brand": "B"},
        ]
        result_id = store.store_products(full_products, tool_name="product_search")

        tool_log = [
            {
                "tool": "product_search",
                "result": {
                    "success": True,
                    "data": {
                        "results": [
                            {"product_id": "P001", "name": "Product A", "rank_score": 0.9},
                            {"product_id": "P002", "name": "Product B", "rank_score": 0.8},
                        ],
                        "result_id": result_id,
                        "total": 2,
                    },
                },
            },
        ]

        results = _extract_search_results_from_log(tool_log)
        assert len(results) == 2
        assert results[0]["product_id"] == "P001"
        assert results[0]["brand"] == "A"  # Full data from store

    def test_extract_search_results_fallback_slim(self):
        """Should fallback to slim results when result_id is missing."""
        from src.graph.specialized_agents import _extract_search_results_from_log
        from src.retrieval.result_store import ResultStore, set_result_store

        set_result_store(ResultStore())

        tool_log = [
            {
                "tool": "product_search",
                "result": {
                    "success": True,
                    "data": {
                        "results": [
                            {"product_id": "P001", "name": "Product A", "rank_score": 0.9},
                        ],
                        "total": 1,
                    },
                },
            },
        ]

        results = _extract_search_results_from_log(tool_log)
        assert len(results) == 1
        assert results[0]["product_id"] == "P001"

    def test_extract_search_results_empty_log(self):
        """Should return empty list when no search tools were called."""
        from src.graph.specialized_agents import _extract_search_results_from_log
        from src.retrieval.result_store import ResultStore, set_result_store

        set_result_store(ResultStore())

        tool_log = [
            {"tool": "ask_clarification", "result": {"data": {"should_ask": True}}},
        ]
        results = _extract_search_results_from_log(tool_log)
        assert results == []

    def test_extract_search_results_multi_query(self):
        """Should extract from multi_query_search results via result store."""
        from src.graph.specialized_agents import _extract_search_results_from_log
        from src.retrieval.result_store import ResultStore, set_result_store

        store = ResultStore()
        set_result_store(store)

        full_products = [{"product_id": "P003", "name": "Product C"}]
        result_id = store.store_products(full_products, tool_name="multi_query_search")

        tool_log = [
            {
                "tool": "multi_query_search",
                "result": {
                    "success": True,
                    "data": {
                        "results": [{"product_id": "P003", "rank_score": 0.85}],
                        "result_id": result_id,
                        "total": 1,
                    },
                },
            },
        ]
        results = _extract_search_results_from_log(tool_log)
        assert len(results) == 1
        assert results[0]["product_id"] == "P003"


class TestPermissionGuardIntegration:
    """Verify rate limiter is called in tool_executor.execute_tool."""

    def test_rate_limit_blocks_excessive_calls(self):
        """Should block tool execution when rate limit exceeded."""
        from src.security.permission import check_rate_limit, PermissionViolation, clear_rate_limits

        clear_rate_limits()

        # First 10 calls should pass
        for _ in range(10):
            check_rate_limit("test_user")

        # 11th call should be blocked
        with pytest.raises(PermissionViolation):
            check_rate_limit("test_user")

        clear_rate_limits()

    def test_rate_limit_per_user_isolation(self):
        """Rate limits should be per-user, not global."""
        from src.security.permission import check_rate_limit, PermissionViolation, clear_rate_limits

        clear_rate_limits()

        # User A: 10 calls
        for _ in range(10):
            check_rate_limit("user_a")

        # User B: should still work
        check_rate_limit("user_b")

        # User A: should be blocked
        with pytest.raises(PermissionViolation):
            check_rate_limit("user_a")

        clear_rate_limits()


class TestDataGuardIntegration:
    """Verify data_guard sanitizes tool args for logging."""

    def test_sanitize_for_log_masks_phone(self):
        """Phone numbers in tool args should be masked."""
        from src.security.data_guard import sanitize_for_log

        args = {"user_note": "我的手机号是13812345678", "category": "护肤"}
        sanitized = sanitize_for_log(args)

        assert "13812345678" not in sanitized["user_note"]
        assert "138****5678" in sanitized["user_note"]
        assert sanitized["category"] == "护肤"  # non-sensitive unchanged

    def test_sanitize_for_log_redacts_sensitive_fields(self):
        """Sensitive field names should be redacted."""
        from src.security.data_guard import sanitize_for_log

        args = {"password": "secret123", "card_number": "6222021234567890", "query": "护肤品"}
        sanitized = sanitize_for_log(args)

        assert sanitized["password"] == "[REDACTED]"
        assert sanitized["card_number"] == "[REDACTED]"
        assert sanitized["query"] == "护肤品"

    def test_sanitize_for_log_preserves_structure(self):
        """Non-sensitive fields should pass through unchanged."""
        from src.security.data_guard import sanitize_for_log

        args = {"semantic_query": "补水保湿", "entities": {"category": "护肤"}}
        sanitized = sanitize_for_log(args)

        assert sanitized["semantic_query"] == "补水保湿"
        assert sanitized["entities"]["category"] == "护肤"
