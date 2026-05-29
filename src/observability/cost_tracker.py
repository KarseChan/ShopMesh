"""LLM Cost Tracker — records token usage and calculates costs per tenant.

Usage data is written to:
1. structlog (always, for observability)
2. PostgreSQL llm_usage table (async, fire-and-forget)
3. In-memory aggregation for budget alerts

Price tables are configured in config.yaml under `cost_tracking`.
"""

import time
from datetime import datetime, timedelta

from src.config import config
from src.observability.logger import get_logger

logger = get_logger("cost_tracker")

# Price config: dollars per 1M tokens (from config.yaml or defaults)
_pricing = config.get("cost_tracking", {}).get("pricing", {})
_PRICE_PER_1M_INPUT = _pricing.get("input_per_1m", 0.15)   # default: $0.15/1M
_PRICE_PER_1M_OUTPUT = _pricing.get("output_per_1m", 0.60)  # default: $0.60/1M

# Budget config
_budget = config.get("cost_tracking", {}).get("budget", {})
_DAILY_BUDGET_CENTS = _budget.get("daily_cents", 10000)  # default: $100/day

# In-memory daily accumulator: tenant_id -> {"input_tokens": N, "output_tokens": N, "cost_cents": N, "date": str}
_daily_costs: dict[str, dict] = {}

# DB availability flag
_db_available: bool | None = None


def _get_today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _calculate_cost_cents(input_tokens: int, output_tokens: int) -> int:
    """Calculate cost in cents from token counts."""
    input_cost = (input_tokens / 1_000_000) * _PRICE_PER_1M_INPUT * 100
    output_cost = (output_tokens / 1_000_000) * _PRICE_PER_1M_OUTPUT * 100
    return int(input_cost + output_cost)


def _try_db() -> bool:
    """Check if DB is available (cached)."""
    global _db_available
    if _db_available is not None:
        return _db_available
    try:
        from src.db.engine import get_session
        session = get_session()
        session.exec("SELECT 1")
        session.close()
        _db_available = True
    except Exception:
        _db_available = False
    return _db_available


def _write_to_db(tenant_id: str, model: str, input_tokens: int,
                 output_tokens: int, cost_cents: int) -> None:
    """Persist usage record to PostgreSQL (best-effort)."""
    if not _try_db():
        return
    try:
        from src.db.engine import get_session
        session = get_session()
        session.exec(
            "INSERT INTO llm_usage (tenant_id, model, input_tokens, output_tokens, cost_cents, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [tenant_id, model, input_tokens, output_tokens, cost_cents, datetime.now()],
        )
        session.commit()
        session.close()
    except Exception as e:
        logger.warning("cost_db_write_failed", error=str(e))


def _update_daily_accumulator(tenant_id: str, input_tokens: int,
                               output_tokens: int, cost_cents: int) -> None:
    """Update in-memory daily cost accumulator and check budget."""
    today = _get_today()
    key = tenant_id or "anonymous"

    if key not in _daily_costs or _daily_costs[key].get("date") != today:
        _daily_costs[key] = {
            "date": today,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_cents": 0,
        }

    acc = _daily_costs[key]
    acc["input_tokens"] += input_tokens
    acc["output_tokens"] += output_tokens
    acc["cost_cents"] += cost_cents

    # Budget alert
    if acc["cost_cents"] >= _DAILY_BUDGET_CENTS:
        logger.warning(
            "daily_budget_exceeded",
            tenant_id=key,
            daily_cost_cents=acc["cost_cents"],
            budget_cents=_DAILY_BUDGET_CENTS,
        )


def record_usage(model: str, input_tokens: int, output_tokens: int,
                 tenant_id: str | None = None) -> None:
    """Record LLM token usage. Non-blocking, fire-and-forget.

    Args:
        model: LLM model name
        input_tokens: Prompt token count
        output_tokens: Completion token count
        tenant_id: Tenant identifier (from auth context if available)
    """
    if input_tokens == 0 and output_tokens == 0:
        return

    cost_cents = _calculate_cost_cents(input_tokens, output_tokens)

    # Structlog (always)
    logger.info(
        "llm_usage",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_cents=cost_cents,
        tenant_id=tenant_id or "anonymous",
    )

    # In-memory accumulator + budget check
    _update_daily_accumulator(tenant_id or "anonymous", input_tokens, output_tokens, cost_cents)

    # DB (best-effort)
    _write_to_db(tenant_id or "anonymous", model, input_tokens, output_tokens, cost_cents)


def get_daily_costs(tenant_id: str | None = None) -> dict:
    """Get today's accumulated costs for a tenant (or all tenants).

    Returns:
        {"tenant_id": str, "date": str, "input_tokens": int, "output_tokens": int, "cost_cents": int}
    """
    today = _get_today()

    if tenant_id:
        acc = _daily_costs.get(tenant_id, {})
        if acc.get("date") == today:
            return {"tenant_id": tenant_id, **acc}
        return {"tenant_id": tenant_id, "date": today, "input_tokens": 0, "output_tokens": 0, "cost_cents": 0}

    # Aggregate all tenants
    total = {"input_tokens": 0, "output_tokens": 0, "cost_cents": 0}
    for acc in _daily_costs.values():
        if acc.get("date") == today:
            total["input_tokens"] += acc["input_tokens"]
            total["output_tokens"] += acc["output_tokens"]
            total["cost_cents"] += acc["cost_cents"]
    return {"tenant_id": "*", "date": today, **total}


def get_costs_from_db(tenant_id: str, start_date: str, end_date: str) -> list[dict]:
    """Query cost history from PostgreSQL.

    Args:
        tenant_id: Tenant identifier
        start_date: ISO date string (inclusive)
        end_date: ISO date string (inclusive)

    Returns:
        List of {"date": str, "model": str, "input_tokens": int, "output_tokens": int, "cost_cents": int}
    """
    if not _try_db():
        return []
    try:
        from src.db.engine import get_session
        session = get_session()
        rows = session.exec(
            "SELECT DATE(created_at) as date, model, SUM(input_tokens), SUM(output_tokens), SUM(cost_cents) "
            "FROM llm_usage WHERE tenant_id = ? AND created_at >= ? AND created_at < ? "
            "GROUP BY DATE(created_at), model ORDER BY date DESC",
            [tenant_id, start_date, end_date + " 23:59:59"],
        ).all()
        session.close()
        return [
            {"date": str(r[0]), "model": r[1], "input_tokens": r[2],
             "output_tokens": r[3], "cost_cents": r[4]}
            for r in rows
        ]
    except Exception as e:
        logger.warning("cost_db_query_failed", error=str(e))
        return []
