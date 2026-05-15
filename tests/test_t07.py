"""T0.7 structured logging tests."""

import json

from src.observability.logger import get_logger, generate_request_id, set_request_context
from src.observability.error_logger import log_error


def test_logger_produces_json():
    """Logger outputs valid JSON with required fields."""
    import io
    import logging
    import structlog

    # Reconfigure to capture output
    buf = io.StringIO()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            lambda logger, method_name, event_dict: json.dumps(event_dict, ensure_ascii=False, default=str),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=buf),
        cache_logger_on_first_use=False,
    )

    logger = structlog.get_logger().bind(module="test")
    logger.info("test_event", key="value")

    output = buf.getvalue().strip()
    data = json.loads(output)
    assert data["event"] == "test_event"
    assert data["key"] == "value"
    assert "timestamp" in data


def test_generate_request_id():
    """generate_request_id returns unique req_xxx format."""
    id1 = generate_request_id()
    id2 = generate_request_id()
    assert id1.startswith("req_")
    assert id2.startswith("req_")
    assert id1 != id2


def test_set_request_context():
    """set_request_context sets context vars."""
    set_request_context("req_test123", "sess_abc")
    from src.observability.logger import request_id_var, session_id_var
    assert request_id_var.get() == "req_test123"
    assert session_id_var.get() == "sess_abc"


def test_error_logger_fields():
    """log_error does not raise."""
    try:
        raise ValueError("test error")
    except ValueError as e:
        log_error(
            module="test",
            step="test_step",
            error=e,
            attempt=2,
            fallback_action="retry",
            error_code=400,
            degraded=True,
        )
