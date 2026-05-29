"""P3.5-T6: Java → Python event consumption tests.

Tests for Celery tasks that handle events published by the Java control plane
via RabbitMQ (events queue).
"""

from unittest.mock import patch, MagicMock, AsyncMock

from src.tasks.event_tasks import handle_user_registered, handle_user_profile_updated


class TestHandleUserRegistered:
    """Tests for the user.registered event handler."""

    @patch("src.tasks.event_tasks._run_async")
    def test_initializes_vector_memory_collection(self, mock_run_async):
        """Should ensure vector memory collection is initialized."""
        mock_run_async.return_value = None

        with patch("src.memory.memory_retriever._ensure_collection", new_callable=AsyncMock) as mock_ensure:
            handle_user_registered("user-123", "tenant-456", "testuser")

            mock_run_async.assert_called_once()
            # Verify _ensure_collection was the coroutine passed to _run_async
            mock_ensure.assert_called_once()

    @patch("src.tasks.event_tasks._run_async")
    def test_logs_event(self, mock_run_async):
        """Should process without error when memory init succeeds."""
        mock_run_async.return_value = None

        with patch("src.memory.memory_retriever._ensure_collection", new_callable=AsyncMock):
            # Should not raise
            handle_user_registered("user-abc", "tenant-xyz", "alice")

    @patch("src.tasks.event_tasks._run_async")
    def test_raises_on_failure_for_celery_retry(self, mock_run_async):
        """Should raise exception so Celery can retry."""
        mock_run_async.side_effect = RuntimeError("Qdrant unavailable")

        with patch("src.memory.memory_retriever._ensure_collection", new_callable=AsyncMock):
            import pytest
            with pytest.raises(RuntimeError, match="Qdrant unavailable"):
                handle_user_registered("user-123", "tenant-456", "testuser")


class TestHandleUserProfileUpdated:
    """Tests for the user.profile.updated event handler."""

    @patch("src.tasks.event_tasks._run_async")
    def test_processes_brand_changes(self, mock_run_async):
        """Should map Java brand changes to Python preference updates."""
        mock_run_async.return_value = None

        with patch("src.memory.user_profile.update_profile_from_preference", new_callable=AsyncMock) as mock_update:
            handle_user_profile_updated(
                "user-123", "tenant-456",
                changes={"preferred_brands": "added:BrandX"}
            )

            mock_update.assert_called_once_with(
                user_id="user-123",
                category="skincare",
                preference_type="brand_preference",
                text="BrandX",
                confidence=0.8,
            )

    @patch("src.tasks.event_tasks._run_async")
    def test_processes_excluded_brands(self, mock_run_async):
        """Should handle excluded_brands changes."""
        mock_run_async.return_value = None

        with patch("src.memory.user_profile.update_profile_from_preference", new_callable=AsyncMock) as mock_update:
            handle_user_profile_updated(
                "user-123", "tenant-456",
                changes={"excluded_brands": "added:BadBrand"}
            )

            mock_update.assert_called_once_with(
                user_id="user-123",
                category="skincare",
                preference_type="brand_preference",
                text="excluded:BadBrand",
                confidence=0.9,
            )

    @patch("src.tasks.event_tasks._run_async")
    def test_no_changes_is_noop(self, mock_run_async):
        """Should return early when changes dict is empty."""
        handle_user_profile_updated("user-123", "tenant-456", changes=None)

        mock_run_async.assert_not_called()

    @patch("src.tasks.event_tasks._run_async")
    def test_raises_on_failure_for_celery_retry(self, mock_run_async):
        """Should raise exception so Celery can retry."""
        mock_run_async.side_effect = RuntimeError("DB unavailable")

        import pytest
        with pytest.raises(RuntimeError, match="DB unavailable"):
            handle_user_profile_updated(
                "user-123", "tenant-456",
                changes={"preferred_brands": "added:BrandX"}
            )
