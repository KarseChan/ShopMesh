"""P4-T5: LLM Cost Tracker tests.

Tests for token usage recording, cost calculation, daily aggregation,
and budget alerting.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.observability.cost_tracker import (
    _calculate_cost_cents,
    _daily_costs,
    get_daily_costs,
    record_usage,
)


@pytest.fixture(autouse=True)
def reset_accumulator():
    """Reset in-memory accumulator between tests."""
    _daily_costs.clear()
    yield
    _daily_costs.clear()


class TestCostCalculation:

    def test_zero_tokens_zero_cost(self):
        assert _calculate_cost_cents(0, 0) == 0

    def test_input_only(self):
        # 1M tokens at $0.15/1M = $0.15 = 15 cents
        cost = _calculate_cost_cents(1_000_000, 0)
        assert cost == 15

    def test_output_only(self):
        # 1M tokens at $0.60/1M = $0.60 = 60 cents
        cost = _calculate_cost_cents(0, 1_000_000)
        assert cost == 60

    def test_both_directions(self):
        # 500K input (7.5 cents) + 200K output (12 cents) = 19.5 cents → 19
        cost = _calculate_cost_cents(500_000, 200_000)
        assert cost == 19

    def test_small_usage(self):
        # 1000 input + 500 output → very small cost
        cost = _calculate_cost_cents(1000, 500)
        assert cost == 0  # rounds to 0 cents


class TestRecordUsage:

    @patch("src.observability.cost_tracker._write_to_db")
    def test_records_to_accumulator(self, mock_db):
        record_usage("gpt-4o-mini", 100_000, 50_000, tenant_id="tenant-1")

        acc = _daily_costs.get("tenant-1")
        assert acc is not None
        assert acc["input_tokens"] == 100_000
        assert acc["output_tokens"] == 50_000
        assert acc["cost_cents"] > 0

    @patch("src.observability.cost_tracker._write_to_db")
    def test_accumulates_multiple_calls(self, mock_db):
        record_usage("gpt-4o-mini", 1000, 500, tenant_id="tenant-1")
        record_usage("gpt-4o-mini", 2000, 1000, tenant_id="tenant-1")

        acc = _daily_costs["tenant-1"]
        assert acc["input_tokens"] == 3000
        assert acc["output_tokens"] == 1500

    @patch("src.observability.cost_tracker._write_to_db")
    def test_skips_zero_usage(self, mock_db):
        record_usage("gpt-4o-mini", 0, 0, tenant_id="tenant-1")

        assert "tenant-1" not in _daily_costs
        mock_db.assert_not_called()

    @patch("src.observability.cost_tracker._write_to_db")
    def test_separates_tenants(self, mock_db):
        record_usage("gpt-4o-mini", 1000, 500, tenant_id="tenant-1")
        record_usage("gpt-4o-mini", 2000, 1000, tenant_id="tenant-2")

        assert _daily_costs["tenant-1"]["input_tokens"] == 1000
        assert _daily_costs["tenant-2"]["input_tokens"] == 2000


class TestGetDailyCosts:

    def test_empty_returns_zeros(self):
        result = get_daily_costs("tenant-1")
        assert result["input_tokens"] == 0
        assert result["output_tokens"] == 0
        assert result["cost_cents"] == 0

    @patch("src.observability.cost_tracker._write_to_db")
    def test_returns_current_day(self, mock_db):
        record_usage("gpt-4o-mini", 1000, 500, tenant_id="tenant-1")

        result = get_daily_costs("tenant-1")
        assert result["tenant_id"] == "tenant-1"
        assert result["input_tokens"] == 1000
        assert result["output_tokens"] == 500

    @patch("src.observability.cost_tracker._write_to_db")
    def test_aggregate_all_tenants(self, mock_db):
        record_usage("gpt-4o-mini", 1000, 500, tenant_id="tenant-1")
        record_usage("gpt-4o-mini", 2000, 1000, tenant_id="tenant-2")

        result = get_daily_costs()  # no tenant_id → aggregate
        assert result["tenant_id"] == "*"
        assert result["input_tokens"] == 3000
        assert result["output_tokens"] == 1500


class TestBudgetAlert:

    @patch("src.observability.cost_tracker._write_to_db")
    @patch("src.observability.cost_tracker._DAILY_BUDGET_CENTS", 100)
    def test_triggers_alert_when_budget_exceeded(self, mock_db):
        """Should log warning when daily cost exceeds budget."""
        # 10M input tokens = $1.50 = 150 cents > 100 cent budget
        with patch("src.observability.cost_tracker.logger") as mock_logger:
            record_usage("gpt-4o-mini", 10_000_000, 0, tenant_id="tenant-1")
            mock_logger.warning.assert_called()
            call_kwargs = mock_logger.warning.call_args
            assert call_kwargs[0][0] == "daily_budget_exceeded"
