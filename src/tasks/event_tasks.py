"""Celery tasks for Java → Python event consumption (events queue).

These tasks handle lifecycle events published by the Java control plane
via RabbitMQ, enabling async cross-service coordination.

Events:
- user.registered: initialize user memory structures after Java-side registration
- user.profile.updated: sync preference changes from Java to Python memory
"""

import asyncio

from src.tasks.celery_app import celery_app
from src.observability.logger import get_logger

logger = get_logger("event_tasks")


def _run_async(coro):
    """Run an async function from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    name="events.user_registered",
    queue="events",
    max_retries=3,
    default_retry_delay=10,
    soft_time_limit=30,
    time_limit=60,
)
def handle_user_registered(user_id: str, tenant_id: str, username: str = "", **kwargs):
    """Handle user.registered event from Java.

    Initializes memory structures for the newly registered user:
    - Ensures the vector memory collection has proper payload indexes
    - Logs the registration for observability

    Args:
        user_id: User's unique ID (UUID)
        tenant_id: User's tenant ID (UUID)
        username: Username (for logging)
    """
    logger.info(
        "event_received",
        event_type="user.registered",
        user_id=user_id,
        tenant_id=tenant_id,
        username=username,
    )

    try:
        # Ensure vector memory collection exists with proper indexes.
        # The unified collection (user_long_term_memories) is shared across all users,
        # but we verify it's initialized so the first real interaction doesn't pay the cost.
        from src.memory.memory_retriever import _ensure_collection
        _run_async(_ensure_collection())

        logger.info(
            "user_registered_processed",
            user_id=user_id,
            tenant_id=tenant_id,
        )
    except Exception as e:
        logger.error(
            "user_registered_failed",
            user_id=user_id,
            error=str(e),
        )
        raise  # Let Celery retry


@celery_app.task(
    name="events.user_profile_updated",
    queue="events",
    max_retries=3,
    default_retry_delay=10,
    soft_time_limit=30,
    time_limit=60,
)
def handle_user_profile_updated(
    user_id: str,
    tenant_id: str,
    changes: dict | None = None,
    **kwargs,
):
    """Handle user.profile.updated event from Java.

    Syncs preference changes from the Java control plane to Python memory:
    - Updates L3 user profile (PostgreSQL user_profiles table)
    - The changes dict contains field-level diffs from Java

    Args:
        user_id: User's unique ID (UUID)
        tenant_id: User's tenant ID (UUID)
        changes: Dict of changed fields, e.g. {"preferred_brands": "added:BrandX"}
    """
    logger.info(
        "event_received",
        event_type="user.profile.updated",
        user_id=user_id,
        tenant_id=tenant_id,
        changes=changes,
    )

    if not changes:
        logger.info("user_profile_updated_no_changes", user_id=user_id)
        return

    try:
        from src.memory.user_profile import update_profile_from_preference

        # Map Java-side changes to Python preference updates
        for field, value in changes.items():
            if field == "preferred_brands" and isinstance(value, str):
                # Format: "added:BrandX" or "removed:BrandY"
                action, brand = value.split(":", 1) if ":" in value else ("added", value)
                preference_type = "brand_preference"
                text = brand
                confidence = 0.8 if action == "added" else 0.5
                # Use "skincare" as default category; Java doesn't always know the category
                _run_async(update_profile_from_preference(
                    user_id=user_id,
                    category="skincare",
                    preference_type=preference_type,
                    text=text,
                    confidence=confidence,
                ))
            elif field == "excluded_brands" and isinstance(value, str):
                action, brand = value.split(":", 1) if ":" in value else ("added", value)
                _run_async(update_profile_from_preference(
                    user_id=user_id,
                    category="skincare",
                    preference_type="brand_preference",
                    text=f"excluded:{brand}" if action == "added" else brand,
                    confidence=0.9,
                ))
            else:
                logger.info(
                    "user_profile_updated_unknown_field",
                    user_id=user_id,
                    field=field,
                    value=value,
                )

        logger.info(
            "user_profile_updated_processed",
            user_id=user_id,
            fields=list(changes.keys()),
        )
    except Exception as e:
        logger.error(
            "user_profile_updated_failed",
            user_id=user_id,
            error=str(e),
        )
        raise  # Let Celery retry
