"""Memory Fallback — graceful degradation when memory services are unavailable.

Degradation levels:
- Level 0: All memory services working (L1 + L2a + L2b + L2c + L3)
- Level 1: L2b summary unavailable → use L2a sliding window only
- Level 2: L2c vector recall unavailable → skip history injection
- Level 3: L2a Redis unavailable → in-memory session only
- Level 4: All memory degraded → stateless mode
"""

from src.observability.logger import get_logger

logger = get_logger("memory_fallback")


class MemoryDegradationManager:
    """Track and manage memory service degradation state."""

    def __init__(self):
        self._degradation_level = 0
        self._service_status: dict[str, bool] = {
            "redis": True,
            "qdrant": True,
            "summary": True,
        }

    @property
    def level(self) -> int:
        return self._degradation_level

    @property
    def is_degraded(self) -> bool:
        return self._degradation_level > 0

    def report_failure(self, service: str) -> None:
        """Report a service failure and update degradation level."""
        if service in self._service_status:
            self._service_status[service] = False
        self._recalculate()

    def report_recovery(self, service: str) -> None:
        """Report a service recovery and update degradation level."""
        if service in self._service_status:
            self._service_status[service] = True
        self._recalculate()

    def _recalculate(self) -> None:
        """Recalculate degradation level from service status."""
        old_level = self._degradation_level

        if not self._service_status["redis"]:
            self._degradation_level = 4 if not self._service_status["qdrant"] else 3
        elif not self._service_status["summary"]:
            self._degradation_level = 2 if not self._service_status["qdrant"] else 1
        elif not self._service_status["qdrant"]:
            self._degradation_level = 2
        else:
            self._degradation_level = 0

        if self._degradation_level != old_level:
            logger.info("degradation_level_changed",
                        old_level=old_level, new_level=self._degradation_level,
                        status=self._service_status)

    def get_available_features(self) -> dict[str, bool]:
        """Return which memory features are currently available."""
        return {
            "working_memory": True,  # L1 always available
            "sliding_window": self._service_status["redis"],  # L2a
            "summary": self._service_status["redis"] and self._service_status["summary"],  # L2b
            "vector_recall": self._service_status["qdrant"],  # L2c
            "user_profile": True,  # L3 (has in-memory fallback)
        }

    def reset(self) -> None:
        """Reset to fully healthy state."""
        self._degradation_level = 0
        self._service_status = {"redis": True, "qdrant": True, "summary": True}
