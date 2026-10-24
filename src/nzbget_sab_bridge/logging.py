"""Structured logging with deterministic secret redaction.

The bridge never logs request/response bodies. This module provides a second
line of defence for accidental secret text in log messages and exception
strings.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_KEYS = (
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "apikey",
    "api_key",
    "api-key",
    "password",
    "passwd",
    "secret",
    "token",
    "sab_api_key",
    "nzbget_password",
    "nzb",
    "nzb_content",
)
_KEY_PATTERN = "|".join(re.escape(key) for key in _SENSITIVE_KEYS)
_ASSIGNMENT_RE = re.compile(rf"(?i)\b({_KEY_PATTERN})\b\s*[:=]\s*(\"[^\"]*\"|'[^']*'|[^\s,;]+)")
_QUERY_RE = re.compile(rf"(?i)([?&](?:{_KEY_PATTERN})=)[^&\s]+")
_BASIC_RE = re.compile(r"(?i)\b(Basic|Bearer)\s+[A-Za-z0-9._~+/=-]+")
_USERINFO_RE = re.compile(r"(?i)\b(https?://)([^/\s:@]+):([^/\s@]+)@")
_SAFE_EXTRA_KEYS = {
    "component",
    "duration_ms",
    "http_method",
    "http_status",
    "matched",
    "matched_capture_route",
    "matched_route",
    "mode",
    "path",
    "query_present",
    "rpc_method",
    "sequence",
    "sab_slots",
    "final_slots",
    "history_statuses",
    "stage",
    "zero_size_slots",
    "total",
    "transport",
}


def redact_text(value: str) -> str:
    """Redact common secret-bearing fragments from a log message."""

    redacted = _USERINFO_RE.sub(r"\1[REDACTED]@", value)
    redacted = _BASIC_RE.sub(r"\1 [REDACTED]", redacted)
    redacted = _QUERY_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _ASSIGNMENT_RE.sub(r"\1=[REDACTED]", redacted)
    return redacted


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value


def _safe_extras(record: logging.LogRecord) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    for key in _SAFE_EXTRA_KEYS:
        if hasattr(record, key):
            extras[key] = redact_value(getattr(record, key))
    return extras


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        payload.update(_safe_extras(record))
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        message = redact_text(record.getMessage())
        extras = " ".join(f"{key}={value}" for key, value in sorted(_safe_extras(record).items()))
        suffix = f" {extras}" if extras else ""
        line = f"{timestamp} {record.levelname:<8} {record.name}: {message}{suffix}"
        if record.exc_info:
            line += "\n" + redact_text(self.formatException(record.exc_info))
        return line


def configure_logging(level: str, log_format: str) -> None:
    """Install a single root handler and quiet HTTP client request logs."""

    formatter: logging.Formatter = JsonFormatter() if log_format == "json" else HumanFormatter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.disabled = False
    # Uvicorn decides whether to emit access lines from logger.hasHandlers(), so
    # disabling propagation alone is not enough: the root handler makes that
    # true. Disable the access logger explicitly to keep query strings out of
    # logs; the application middleware emits its own sanitized request summary.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
    access_logger.disabled = True
    for name in ("httpx", "httpcore", "hpack"):
        logging.getLogger(name).setLevel(logging.WARNING)
