"""L3 Behavior Tracker — implicit feedback from user actions to Profile updates.

Processes behavior signals (click/select/reject/dwell) and updates L3 Profile
via EMA price range narrowing, brand preference reinforcement, and negative preference.

All updates are async and non-blocking to the main request path.
"""

from dataclasses import dataclass, field

from src.memory.user_profile import get_profile, update_profile
from src.observability.logger import get_logger

logger = get_logger("behavior_tracker")


@dataclass
class BehaviorSignal:
    """A user behavior event captured from the frontend."""
    user_id: str
    category: str
    action: str  # "click" | "select" | "reject" | "dwell"
    product_price: float | None = None
    product_brand: str | None = None
    product_id: str | None = None
    duration_ms: int | None = None


async def process_signal(signal: BehaviorSignal) -> None:
    """Process a behavior signal and update L3 Profile accordingly.

    Actions:
    - select: user chose a product → narrow price range via EMA, reinforce brand
    - click: user clicked a product card → reinforce brand preference
    - reject: user explicitly rejected → add negative brand
    - dwell: user lingered > 5s on a product → mild interest signal (future use)
    """
    profile = get_profile(signal.user_id, signal.category)
    updates = {}

    if signal.action == "select" and signal.product_price is not None:
        # EMA price range narrowing
        price_range = profile.get("price_range")
        alpha = 0.3  # smoothing factor
        if price_range:
            new_min = price_range[0] * (1 - alpha) + signal.product_price * alpha
            new_max = price_range[1] * (1 - alpha) + signal.product_price * alpha
            updates["price_range"] = (round(new_min), round(new_max))
        else:
            # First selection: ±30% as initial range
            updates["price_range"] = (
                round(signal.product_price * 0.7),
                round(signal.product_price * 1.3),
            )
        logger.info("price_range_updated",
                     user_id=signal.user_id,
                     category=signal.category,
                     price=signal.product_price,
                     new_range=updates.get("price_range"))

    if signal.action == "select" and signal.product_brand:
        # Reinforce brand preference on selection
        brands = list(profile.get("preferred_brands", []))
        if signal.product_brand not in brands:
            brands.append(signal.product_brand)
            updates["preferred_brands"] = brands[-5:]  # keep max 5
            logger.info("brand_reinforced",
                         user_id=signal.user_id,
                         brand=signal.product_brand,
                         action="select")

    elif signal.action == "click" and signal.product_brand:
        # Mild brand signal on click
        brands = list(profile.get("preferred_brands", []))
        if signal.product_brand not in brands:
            brands.append(signal.product_brand)
            updates["preferred_brands"] = brands[-5:]
            logger.info("brand_reinforced",
                         user_id=signal.user_id,
                         brand=signal.product_brand,
                         action="click")

    elif signal.action == "reject" and signal.product_brand:
        # Negative brand preference
        negative = list(profile.get("negative_brands", []))
        if signal.product_brand not in negative:
            negative.append(signal.product_brand)
            updates["negative_brands"] = negative[-3:]  # keep max 3
            logger.info("brand_rejected",
                         user_id=signal.user_id,
                         brand=signal.product_brand)

    if updates:
        update_profile(signal.user_id, signal.category, updates)
        logger.info("profile_updated_from_behavior",
                     user_id=signal.user_id,
                     category=signal.category,
                     action=signal.action,
                     updated_keys=list(updates.keys()))
