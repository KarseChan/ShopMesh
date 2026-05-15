"""L3 User Profile — conditional profiles with category and scenario isolation.

Storage strategy:
- DB (PostgreSQL): persistent per-user, per-category profiles (if available)
- Memory fallback: in-memory dict when DB is unavailable

Profile structure per (user_id, category):
    price_sensitivity: float (0-1, higher = more price sensitive)
    preferred_brands: list[str]
    visit_count: int
    scenario: str | None (自用/送礼/...)
    price_range: (min, max) or None
"""

import json
from datetime import datetime

from src.observability.logger import get_logger

logger = get_logger("user_profile")

# In-memory fallback when DB is unavailable
_profiles: dict[str, dict] = {}
_db_available: bool | None = None


def _profile_key(user_id: str, category: str) -> str:
    return f"{user_id}:{category}"


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


def get_profile(user_id: str, category: str) -> dict:
    """Get user profile for a specific category.

    Returns:
        {
            "price_sensitivity": float,
            "preferred_brands": list[str],
            "visit_count": int,
            "scenario": str | None,
            "price_range": (min, max) | None,
        }
    """
    key = _profile_key(user_id, category)

    # Try memory first (fast path)
    if key in _profiles:
        return dict(_profiles[key])

    # Try DB
    if _try_db():
        try:
            from src.db.engine import get_session
            from src.db.models import UserProfile
            session = get_session()
            profile = session.exec(
                UserProfile.select().where(
                    UserProfile.user_id == user_id,
                    UserProfile.category == category
                )
            ).first()
            session.close()
            if profile:
                result = {
                    "price_sensitivity": profile.price_sensitivity,
                    "preferred_brands": json.loads(profile.preferred_brands) if profile.preferred_brands else [],
                    "visit_count": profile.visit_count,
                    "scenario": None,
                    "price_range": None,
                }
                _profiles[key] = result
                return result
        except Exception as e:
            logger.warning("db_profile_read_failed", error=str(e))

    # Default profile
    default = {
        "price_sensitivity": 0.5,
        "preferred_brands": [],
        "visit_count": 0,
        "scenario": None,
        "price_range": None,
    }
    _profiles[key] = default
    return dict(default)


def update_profile(user_id: str, category: str, updates: dict) -> dict:
    """Update user profile for a category.

    Returns the updated profile.
    """
    profile = get_profile(user_id, category)
    profile.update(updates)
    key = _profile_key(user_id, category)
    _profiles[key] = profile

    # Try to persist to DB
    if _try_db():
        try:
            from src.db.engine import get_session
            from src.db.models import UserProfile
            session = get_session()
            existing = session.exec(
                UserProfile.select().where(
                    UserProfile.user_id == user_id,
                    UserProfile.category == category
                )
            ).first()
            if existing:
                existing.price_sensitivity = profile["price_sensitivity"]
                existing.preferred_brands = json.dumps(profile["preferred_brands"], ensure_ascii=False)
                existing.visit_count = profile["visit_count"]
                existing.updated_at = datetime.utcnow()
            else:
                new_profile = UserProfile(
                    user_id=user_id,
                    category=category,
                    price_sensitivity=profile["price_sensitivity"],
                    preferred_brands=json.dumps(profile["preferred_brands"], ensure_ascii=False),
                    visit_count=profile["visit_count"],
                )
                session.add(new_profile)
            session.commit()
            session.close()
        except Exception as e:
            logger.warning("db_profile_write_failed", error=str(e))

    logger.info("profile_updated", user_id=user_id, category=category, keys=list(updates.keys()))
    return profile


def record_visit(user_id: str, category: str, scenario: str | None = None) -> dict:
    """Record a user visit to a category. Increments visit count.

    Returns the updated profile.
    """
    profile = get_profile(user_id, category)
    updates = {"visit_count": profile["visit_count"] + 1}
    if scenario:
        updates["scenario"] = scenario
    return update_profile(user_id, category, updates)


def get_global_profile(user_id: str) -> dict:
    """Get aggregated profile across all categories for a user."""
    if not _profiles:
        return {"price_sensitivity": 0.5, "preferred_brands": [], "total_visits": 0}

    user_profiles = {k: v for k, v in _profiles.items() if k.startswith(f"{user_id}:")}
    if not user_profiles:
        return {"price_sensitivity": 0.5, "preferred_brands": [], "total_visits": 0}

    all_brands = set()
    total_visits = 0
    sensitivities = []
    for p in user_profiles.values():
        sensitivities.append(p["price_sensitivity"])
        all_brands.update(p["preferred_brands"])
        total_visits += p["visit_count"]

    return {
        "price_sensitivity": sum(sensitivities) / len(sensitivities),
        "preferred_brands": list(all_brands),
        "total_visits": total_visits,
    }


def format_profile_summary(profile: dict, category: str | None = None) -> str:
    """Format a profile into a human-readable summary for prompt injection."""
    parts = []
    if profile.get("preferred_brands"):
        parts.append(f"偏好品牌: {', '.join(profile['preferred_brands'][:3])}")
    sensitivity = profile.get("price_sensitivity")
    if sensitivity is not None and sensitivity != 0.5:
        level = "高" if sensitivity > 0.7 else "低"
        parts.append(f"价格敏感度: {level}")
    if profile.get("price_range"):
        parts.append(f"预算区间: {profile['price_range'][0]}-{profile['price_range'][1]}元")
    if profile.get("scenario"):
        parts.append(f"使用场景: {profile['scenario']}")
    if profile.get("visit_count", 0) > 0:
        parts.append(f"浏览次数: {profile['visit_count']}")
    if category:
        parts.insert(0, f"[{category}]")
    return "，".join(parts) if parts else ""
