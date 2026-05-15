"""Unified structured logger — JSON format + dual ID injection + request context.

Usage:
    from src.observability.logger import get_logger
    logger = get_logger("entity_extractor")
    logger.info("entities_extracted", entities={"category": "奶茶"}, duration_ms=45)
"""

import contextvars
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

import structlog

from src.config import config

# Context vars for request-scoped IDs
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default="")

_configured = False


def _configure_structlog():
    global _configured
    if _configured:
        return

    log_cfg = config.get("logging", {})
    level = log_cfg.get("level", "INFO").upper()
    log_level = getattr(logging, level, logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _inject_ids,
            _json_renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    _configured = True


def _inject_ids(logger, method_name, event_dict):
    """Inject request_id and session_id from context vars."""
    req_id = request_id_var.get("")
    sess_id = session_id_var.get("")
    if req_id:
        event_dict["request_id"] = req_id
    if sess_id:
        event_dict["session_id"] = sess_id
    return event_dict


def _json_renderer(logger, method_name, event_dict):
    """Render log entry as JSON string."""
    return json.dumps(event_dict, ensure_ascii=False, default=str)


def get_logger(module: str) -> structlog.BoundLogger:
    """Get a structured logger bound to a module name."""
    _configure_structlog()
    return structlog.get_logger().bind(module=module)


def generate_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:8]}"


def set_request_context(request_id: str, session_id: str = ""):
    """Set request-scoped context vars for the current async task."""
    request_id_var.set(request_id)
    if session_id:
        session_id_var.set(session_id)
