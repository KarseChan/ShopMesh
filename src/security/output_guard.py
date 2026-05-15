"""Output Guard — reasoning safety layer (deterministic rule checks, no LLM calls).

Checks (< 1ms each, no additional LLM calls):
- Hallucination detection: item_id must exist in search results
- Price boundary: price >= historical_min * 0.3
- Coverage: output items <= search result items
- Source tracing: each recommendation must have traceable item_id
"""

from src.observability.logger import get_logger

logger = get_logger("output_guard")

PRICE_FLOOR_RATIO = 0.3  # Alert if price < 30% of historical min


class OutputViolation(Exception):
    """Raised when output fails safety checks."""

    def __init__(self, reason: str, violation_type: str = "hallucination"):
        self.reason = reason
        self.violation_type = violation_type  # hallucination | price_anomaly | coverage
        super().__init__(reason)


def check_item_traceability(
    output_items: list[dict],
    search_results: list[dict],
) -> None:
    """Verify every output item_id exists in search results.

    This is the core hallucination check: < 1ms, no LLM calls.
    """
    search_ids = set()
    for r in search_results:
        payload = r.get("payload", r)
        pid = payload.get("product_id", r.get("id", ""))
        if pid:
            search_ids.add(pid)

    for item in output_items:
        item_id = item.get("product_id", item.get("id", ""))
        if item_id and item_id not in search_ids:
            logger.warning("hallucination_detected", item_id=item_id)
            raise OutputViolation(
                f"推荐商品 {item_id} 不在检索结果中",
                violation_type="hallucination",
            )


def check_price_boundary(
    output_items: list[dict],
    historical_min: float | None = None,
) -> None:
    """Check prices are not suspiciously low."""
    if historical_min is None or historical_min <= 0:
        return

    floor = historical_min * PRICE_FLOOR_RATIO
    for item in output_items:
        price = item.get("final_price", item.get("price", 0))
        if price > 0 and price < floor:
            logger.warning("price_anomaly", price=price, floor=floor,
                           historical_min=historical_min)
            raise OutputViolation(
                f"价格 {price} 低于历史最低价的 {PRICE_FLOOR_RATIO*100:.0f}%",
                violation_type="price_anomaly",
            )


def check_coverage(
    output_items: list[dict],
    search_results: list[dict],
) -> None:
    """Output count must not exceed search result count."""
    if len(output_items) > len(search_results):
        logger.warning("coverage_violation", output=len(output_items),
                       search=len(search_results))
        raise OutputViolation(
            f"输出 {len(output_items)} 项超过检索结果 {len(search_results)} 项",
            violation_type="coverage",
        )


def validate_output(
    output_items: list[dict],
    search_results: list[dict],
    historical_min: float | None = None,
) -> dict:
    """Run all output safety checks.

    Returns:
        {"safe": True} if all checks pass

    Raises:
        OutputViolation if any check fails
    """
    check_item_traceability(output_items, search_results)
    check_price_boundary(output_items, historical_min)
    check_coverage(output_items, search_results)

    return {"safe": True}
