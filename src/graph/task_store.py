"""Task Store — Redis-persisted DAG task state management.

Provides claim/complete semantics for DAG tasks, enabling:
- Cross-session DAG persistence
- Dependency-aware task blocking
- Task lifecycle tracking (pending → claimed → completed)

Key naming: dag:{session_id}:{dag_id}, task:{session_id}:{dag_id}:{task_id}
TTL: 24 hours (aligned with session memory)
"""

import json
import time
import random

from src.db.redis_client import get_redis
from src.observability.logger import get_logger

logger = get_logger("task_store")

# TTL for DAG and task keys — config-driven
from src.config import config as _cfg
_DAG_TTL = _cfg.get("session", {}).get("dag_ttl", 86400)


def _dag_key(session_id: str, dag_id: str) -> str:
    return f"dag:{session_id}:{dag_id}"


def _task_key(session_id: str, dag_id: str, task_id: str) -> str:
    return f"task:{session_id}:{dag_id}:{task_id}"


class TaskStore:
    """Redis-backed task state store for DAG execution."""

    def __init__(self):
        self._redis = get_redis()

    async def save_dag(self, session_id: str, dag_id: str, dag: list[dict]) -> None:
        """Save DAG metadata and initial task states to Redis."""
        pipe = self._redis.pipeline()

        # Save DAG metadata
        dag_meta = {
            "dag_id": dag_id,
            "session_id": session_id,
            "created_at": str(time.time()),
            "status": "pending",
            "task_count": str(len(dag)),
        }
        pipe.hset(_dag_key(session_id, dag_id), mapping=dag_meta)
        pipe.expire(_dag_key(session_id, dag_id), _DAG_TTL)

        # Save each task's initial state
        for task in dag:
            task_id = task["task_id"]
            task_state = {
                "task_id": task_id,
                "status": "pending",
                "claimed_at": "",
                "completed_at": "",
                "depends_on": json.dumps(task.get("depends_on", [])),
                "result_summary": "",
            }
            pipe.hset(_task_key(session_id, dag_id, task_id), mapping=task_state)
            pipe.expire(_task_key(session_id, dag_id, task_id), _DAG_TTL)

        await pipe.execute()
        logger.info("dag_saved", dag_id=dag_id, task_count=len(dag))

    async def load_dag(self, session_id: str, dag_id: str) -> list[dict] | None:
        """Load DAG structure from Redis. Returns None if not found."""
        dag_meta = await self._redis.hgetall(_dag_key(session_id, dag_id))
        if not dag_meta:
            return None

        task_count = int(dag_meta.get("task_count", 0))
        tasks = []

        # Scan for task keys (they follow the pattern task:{session}:{dag}:*)
        pattern = f"task:{session_id}:{dag_id}:*"
        async for key in self._redis.scan_iter(match=pattern):
            task_data = await self._redis.hgetall(key)
            if task_data:
                task_data["depends_on"] = json.loads(task_data.get("depends_on", "[]"))
                tasks.append(task_data)

        return tasks if tasks else None

    async def claim_task(self, session_id: str, dag_id: str, task_id: str) -> bool:
        """Attempt to claim a pending task.

        Returns True if successfully claimed, False if:
        - Task not found
        - Task already claimed/completed
        - Dependencies not yet completed (blocked)
        """
        task_key = _task_key(session_id, dag_id, task_id)
        task_data = await self._redis.hgetall(task_key)

        if not task_data:
            logger.warning("task_not_found", task_id=task_id)
            return False

        # Check current status
        current_status = task_data.get("status", "")
        if current_status != "pending":
            logger.info("task_already_claimed", task_id=task_id, status=current_status)
            return False

        # Check dependencies
        depends_on = json.loads(task_data.get("depends_on", "[]"))
        if depends_on:
            for dep_id in depends_on:
                dep_status = await self.get_task_status(session_id, dag_id, dep_id)
                if dep_status != "completed":
                    logger.info("task_blocked_by_dependency",
                                task_id=task_id, blocked_by=dep_id)
                    return False

        # Claim the task
        await self._redis.hset(task_key, mapping={
            "status": "claimed",
            "claimed_at": str(time.time()),
        })
        await self._redis.expire(task_key, _DAG_TTL)

        # Update DAG status to running
        await self._redis.hset(_dag_key(session_id, dag_id), "status", "running")

        logger.info("task_claimed", task_id=task_id)
        return True

    async def complete_task(self, session_id: str, dag_id: str, task_id: str,
                           result_summary: str = "") -> list[str]:
        """Mark a task as completed and return newly unblocked tasks."""
        task_key = _task_key(session_id, dag_id, task_id)

        # Update task status
        await self._redis.hset(task_key, mapping={
            "status": "completed",
            "completed_at": str(time.time()),
            "result_summary": result_summary[:500],  # Truncate long results
        })
        await self._redis.expire(task_key, _DAG_TTL)

        # Find newly unblocked tasks
        unblocked = await self._find_unblocked_tasks(session_id, dag_id)

        # Check if all tasks are completed
        all_completed = await self._check_all_completed(session_id, dag_id)
        if all_completed:
            await self._redis.hset(_dag_key(session_id, dag_id), "status", "completed")

        logger.info("task_completed", task_id=task_id, unblocked=unblocked)
        return unblocked

    async def get_task_status(self, session_id: str, dag_id: str,
                              task_id: str) -> str:
        """Get the current status of a task."""
        status = await self._redis.hget(
            _task_key(session_id, dag_id, task_id), "status")
        return status or "unknown"

    async def get_blocked_tasks(self, session_id: str, dag_id: str) -> list[str]:
        """Get all tasks that are blocked by incomplete dependencies."""
        blocked = []
        pattern = f"task:{session_id}:{dag_id}:*"

        async for key in self._redis.scan_iter(match=pattern):
            task_data = await self._redis.hgetall(key)
            if task_data.get("status") != "pending":
                continue

            depends_on = json.loads(task_data.get("depends_on", "[]"))
            for dep_id in depends_on:
                dep_status = await self.get_task_status(session_id, dag_id, dep_id)
                if dep_status != "completed":
                    blocked.append(task_data.get("task_id", ""))
                    break

        return blocked

    async def _find_unblocked_tasks(self, session_id: str, dag_id: str) -> list[str]:
        """Find tasks that are now unblocked (all deps completed)."""
        unblocked = []
        pattern = f"task:{session_id}:{dag_id}:*"

        async for key in self._redis.scan_iter(match=pattern):
            task_data = await self._redis.hgetall(key)
            if task_data.get("status") != "pending":
                continue

            depends_on = json.loads(task_data.get("depends_on", "[]"))
            if not depends_on:
                continue  # No dependencies, already runnable

            all_deps_completed = True
            for dep_id in depends_on:
                dep_status = await self.get_task_status(session_id, dag_id, dep_id)
                if dep_status != "completed":
                    all_deps_completed = False
                    break

            if all_deps_completed:
                unblocked.append(task_data.get("task_id", ""))

        return unblocked

    async def _check_all_completed(self, session_id: str, dag_id: str) -> bool:
        """Check if all tasks in the DAG are completed."""
        pattern = f"task:{session_id}:{dag_id}:*"

        async for key in self._redis.scan_iter(match=pattern):
            status = await self._redis.hget(key, "status")
            if status != "completed":
                return False

        return True


# Singleton instance
_task_store: TaskStore | None = None


def get_task_store() -> TaskStore:
    """Get the singleton TaskStore instance."""
    global _task_store
    if _task_store is None:
        _task_store = TaskStore()
    return _task_store


def generate_dag_id() -> str:
    """Generate a unique DAG ID."""
    return f"dag_{int(time.time())}_{random.randint(0, 9999):04d}"
