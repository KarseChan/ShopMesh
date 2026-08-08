"""Item catalog — unified lookup over products + 秒送 dishes (P2).

The transaction machinery (cart_store / order_service) resolves an orderable
item's name/price/stock by id via a `_product()` helper. 秒送 dishes are just
items with `item_type='dish'` and a `merchant_id`, living in data/mock_dishes.json
(same shape as products). This module merges both sources so the EXISTING
cart/order/payment flow works for dishes verbatim — no rewrite of the paradigm.

Products win on id collision (there is none in practice: product ids are
`batch_*`, dish ids are `<merchant_id>_d<n>`).
"""

import json
from pathlib import Path

from src.observability.logger import get_logger

logger = get_logger("item_catalog")

_DISHES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "mock_dishes.json"

_DISHES_BY_ID: dict[str, dict] | None = None
_DISHES_BY_MERCHANT: dict[str, list[dict]] | None = None


def _load_dishes() -> None:
    global _DISHES_BY_ID, _DISHES_BY_MERCHANT
    if _DISHES_BY_ID is not None:
        return
    _DISHES_BY_ID = {}
    _DISHES_BY_MERCHANT = {}
    try:
        with open(_DISHES_PATH, encoding="utf-8") as f:
            dishes = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        # Dishes are optional — degrade to empty so product flows are unaffected.
        logger.warning("dishes_load_failed", path=str(_DISHES_PATH), error=str(e))
        dishes = []
    for d in dishes:
        pid = d.get("product_id")
        if not pid:
            continue
        _DISHES_BY_ID[pid] = d
        _DISHES_BY_MERCHANT.setdefault(d.get("merchant_id", ""), []).append(d)


def find_item(item_id: str) -> dict | None:
    """Resolve an orderable item by id: product first, then 秒送 dish."""
    if not item_id:
        return None
    from src.tools.search_tool import load_products
    # Product path (cached by callers' own maps historically; here just scan once).
    global _PRODUCT_BY_ID
    if _PRODUCT_BY_ID is None:
        _PRODUCT_BY_ID = {p["product_id"]: p for p in load_products() if p.get("product_id")}
    hit = _PRODUCT_BY_ID.get(item_id)
    if hit:
        return hit
    _load_dishes()
    return _DISHES_BY_ID.get(item_id)


def get_menu(merchant_id: str) -> list[dict]:
    """Return the dish list for a merchant (its orderable menu)."""
    _load_dishes()
    return list(_DISHES_BY_MERCHANT.get(merchant_id, []))


_PRODUCT_BY_ID: dict[str, dict] | None = None
