"""P3.5-T7: End-to-end integration tests for Java ↔ Python event bridge.

Tests the full event flow configuration without requiring live services:
- Celery app configuration (task routes, queues)
- Event task registration and naming
- Event task logic with mocked dependencies
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestCeleryEventConfiguration:
    """Verify Celery app is configured for the events queue."""

    def test_event_tasks_routed_to_events_queue(self):
        """Event tasks should be routed to the 'events' queue."""
        from src.tasks.celery_app import celery_app

        routes = celery_app.conf.task_routes
        assert "src.tasks.event_tasks.*" in routes
        assert routes["src.tasks.event_tasks.*"]["queue"] == "events"

    def test_memory_tasks_routed_to_memory_queue(self):
        """Memory tasks should be routed to the 'memory' queue."""
        from src.tasks.celery_app import celery_app

        routes = celery_app.conf.task_routes
        assert "src.tasks.memory_tasks.*" in routes
        assert routes["src.tasks.memory_tasks.*"]["queue"] == "memory"

    def test_cleanup_tasks_routed_to_cleanup_queue(self):
        """Cleanup tasks should be routed to the 'cleanup' queue."""
        from src.tasks.celery_app import celery_app

        routes = celery_app.conf.task_routes
        assert "src.tasks.cleanup_tasks.*" in routes
        assert routes["src.tasks.cleanup_tasks.*"]["queue"] == "cleanup"

    def test_default_queue_configured(self):
        """Default queue should be 'default'."""
        from src.tasks.celery_app import celery_app

        assert celery_app.conf.task_default_queue == "default"


class TestEventTaskRegistration:
    """Verify event tasks are registered with correct names."""

    def test_user_registered_task_exists(self):
        """events.user_registered task should be registered."""
        from src.tasks.event_tasks import handle_user_registered

        assert handle_user_registered.name == "events.user_registered"

    def test_user_profile_updated_task_exists(self):
        """events.user_profile_updated task should be registered."""
        from src.tasks.event_tasks import handle_user_profile_updated

        assert handle_user_profile_updated.name == "events.user_profile_updated"

    def test_event_tasks_in_autodiscovery(self):
        """Event tasks should be discoverable by Celery autodiscovery."""
        from src.tasks.celery_app import celery_app

        # Force task registry population
        celery_app.loader.import_default_modules()
        registered = list(celery_app.tasks.keys())

        assert "events.user_registered" in registered
        assert "events.user_profile_updated" in registered


class TestEventFlowIntegration:
    """Integration tests for the full event processing flow."""

    @patch("src.tasks.event_tasks._run_async")
    def test_user_registered_flow(self, mock_run_async):
        """Full flow: user.registered event → memory collection init."""
        mock_run_async.return_value = None

        with patch("src.memory.memory_retriever._ensure_collection", new_callable=AsyncMock):
            from src.tasks.event_tasks import handle_user_registered
            handle_user_registered("u-123", "t-456", "alice")

            # Verify async work was dispatched
            mock_run_async.assert_called_once()

    @patch("src.tasks.event_tasks._run_async")
    def test_user_profile_updated_flow(self, mock_run_async):
        """Full flow: user.profile.updated event → profile sync."""
        mock_run_async.return_value = None

        with patch("src.memory.user_profile.update_profile_from_preference", new_callable=AsyncMock):
            from src.tasks.event_tasks import handle_user_profile_updated
            handle_user_profile_updated(
                "u-123", "t-456",
                changes={"preferred_brands": "added:SK-II"}
            )

            # Verify profile update was dispatched
            mock_run_async.assert_called_once()

    @patch("src.tasks.event_tasks._run_async")
    def test_user_registered_failure_triggers_retry(self, mock_run_async):
        """Failure in user.registered handler should propagate for Celery retry."""
        mock_run_async.side_effect = ConnectionError("Qdrant down")

        with patch("src.memory.memory_retriever._ensure_collection", new_callable=AsyncMock):
            from src.tasks.event_tasks import handle_user_registered
            with pytest.raises(ConnectionError):
                handle_user_registered("u-123", "t-456", "alice")

    @patch("src.tasks.event_tasks._run_async")
    def test_user_profile_updated_no_changes_is_noop(self, mock_run_async):
        """Empty changes dict should not trigger any profile updates."""
        from src.tasks.event_tasks import handle_user_profile_updated
        handle_user_profile_updated("u-123", "t-456", changes=None)

        mock_run_async.assert_not_called()

    @patch("src.tasks.event_tasks._run_async")
    def test_multiple_changes_processed(self, mock_run_async):
        """Multiple changes should each trigger a profile update."""
        mock_run_async.return_value = None

        with patch("src.memory.user_profile.update_profile_from_preference", new_callable=AsyncMock):
            from src.tasks.event_tasks import handle_user_profile_updated
            handle_user_profile_updated(
                "u-123", "t-456",
                changes={
                    "preferred_brands": "added:SK-II",
                    "excluded_brands": "added:BadBrand",
                }
            )

            # Two changes → two update calls
            assert mock_run_async.call_count == 2
