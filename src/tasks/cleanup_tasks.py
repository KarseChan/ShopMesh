"""Celery tasks for periodic cleanup — memory decay, expired sessions.

These tasks run on the 'cleanup' queue and are scheduled via Celery Beat.
"""

import asyncio

from src.tasks.celery_app import celery_app


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    name="tasks.cleanup.expired_memories",
    queue="cleanup",
    max_retries=1,
    soft_time_limit=120,
    time_limit=180,
)
def cleanup_expired_memories():
    """Periodic cleanup of expired vector memories (Celery Beat schedule)."""
    from src.memory.memory_decay import cleanup_expired_all
    count = _run_async(cleanup_expired_all())
    return {"cleaned": count}


@celery_app.task(
    name="tasks.cleanup.user_memories",
    queue="cleanup",
    max_retries=1,
    soft_time_limit=60,
    time_limit=90,
)
def cleanup_user_memories(user_id: str):
    """Purge all memories for a specific user (e.g., account deletion)."""
    from src.memory.memory_decay import cleanup_user_all
    count = _run_async(cleanup_user_all(user_id))
    return {"user_id": user_id, "cleaned": count}


@celery_app.task(
    name="tasks.cleanup.cancel_expired_orders",
    queue="cleanup",
    max_retries=1,
    soft_time_limit=60,
    time_limit=90,
)
def cancel_expired_orders():
    """超时未支付订单自动取消 + 释放库存(Celery Beat 周期扫描)。"""
    from src.config import config
    from src.skills import order_service
    timeout = int(config.get("payment", {}).get("order_timeout_sec", 900))
    order_ids = order_service.list_expired_awaiting(timeout)
    cancelled = 0
    for oid in order_ids:
        res = _run_async(order_service.cancel_order(oid))
        if res.get("ok"):
            cancelled += 1
    return {"scanned": len(order_ids), "cancelled": cancelled}


# Celery Beat schedule
celery_app.conf.beat_schedule = {
    "cleanup-expired-memories-daily": {
        "task": "tasks.cleanup.expired_memories",
        "schedule": 86400.0,  # every 24 hours
    },
    "cancel-expired-orders": {
        "task": "tasks.cleanup.cancel_expired_orders",
        "schedule": 300.0,  # 每 5 分钟扫描超时未支付订单
    },
}
