"""Celery application — configured from config.yaml.

Broker: RabbitMQ
Result backend: Redis (lightweight task results)
"""

from celery import Celery

from src.config import config

_broker_url = config.get("celery", {}).get("broker_url", "amqp://guest:guest@localhost:5672//")
_result_backend = config.get("celery", {}).get("result_backend", config["redis"]["url"])

celery_app = Celery(
    "shopmesh",
    broker=_broker_url,
    backend=_result_backend,
    # 显式导入任务模块,任务才会注册。autodiscover_tasks 默认找 src.tasks.tasks
    # (不存在),会导致 "Received unregistered task"。
    include=[
        "src.tasks.memory_tasks",
        "src.tasks.cleanup_tasks",
        "src.tasks.event_tasks",
    ],
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timezone
    timezone="Asia/Shanghai",
    enable_utc=True,

    # Reliability
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,

    # Result expiry
    result_expires=3600,  # 1 hour

    # Task routes — 按任务的显式名(tasks.<domain>.*)匹配,而非模块路径
    task_routes={
        "tasks.memory.*": {"queue": "memory"},
        "tasks.cleanup.*": {"queue": "cleanup"},
        "tasks.events.*": {"queue": "events"},
    },

    # Default queue
    task_default_queue="default",
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["src.tasks"])
