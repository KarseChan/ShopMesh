"""Unified structured logger — JSON format + dual ID injection + request context.

Usage:
    from src.observability.logger import get_logger
    logger = get_logger("entity_extractor")
    logger.info("entities_extracted", entities={"category": "奶茶"}, duration_ms=45)
"""

import contextvars
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

import structlog

from src.config import config

# Context vars for request-scoped IDs
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default="")

_configured = False
_log_file = None


class _TeeWriter:
    """Write to both stdout and a file simultaneously.

    stdout write is encoding-robust: on Windows the console defaults to GBK, which
    can't encode chars like '¥' (U+00A5) that show up in LLM output — an unguarded
    write would raise UnicodeEncodeError and **crash the request being logged**
    (real bug: the LLM 组单 path died mid-stream on a '¥' in the model's reply).
    The file is always UTF-8, so it keeps the exact message; stdout degrades to a
    best-effort replacement rather than throwing.
    """

    def __init__(self, file):
        self._file = file

    def write(self, msg):
        try:
            sys.stdout.write(msg)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            sys.stdout.write(msg.encode(enc, errors="replace").decode(enc, errors="replace"))
        self._file.write(msg)
        self._file.flush()

    def flush(self):
        sys.stdout.flush()
        self._file.flush()


def _configure_structlog():
    global _configured, _log_file
    if _configured:
        return

    # Windows console defaults to GBK → LLM output with '¥' etc. would crash the
    # logger. Prefer real UTF-8 stdout (correct glyphs); the _TeeWriter guard is the
    # fallback if reconfigure isn't available. Independent of PYTHONIOENCODING so the
    # app is robust regardless of how the server was launched.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    log_cfg = config.get("logging", {})
    level = log_cfg.get("level", "INFO").upper()
    log_level = getattr(logging, level, logging.INFO)

    # Setup log file
    log_path = log_cfg.get("file_path", "logs/app.jsonl")
    log_dir = os.path.dirname(log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    _log_file = open(log_path, "a", encoding="utf-8")

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
        logger_factory=structlog.PrintLoggerFactory(file=_TeeWriter(_log_file)),
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
