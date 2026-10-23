from __future__ import annotations

import io
import json
import logging

from nzbget_sab_bridge.logging import (
    HumanFormatter,
    JsonFormatter,
    configure_logging,
    redact_text,
)
from tests.conftest import (
    FAKE_CAPTURE_PASSWORD,
    FAKE_INDEXER_URL,
    FAKE_NZB_BASE64,
    FAKE_SAB_API_KEY,
)


def _format(formatter: logging.Formatter, message: str, **extra: object) -> str:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return formatter.format(record)


def test_redact_assignments_and_headers() -> None:
    text = (
        f"Authorization: Basic abc123 apikey={FAKE_SAB_API_KEY} "
        f"password={FAKE_CAPTURE_PASSWORD} Cookie: {FAKE_INDEXER_URL}"
    )
    redacted = redact_text(text)
    assert "abc123" not in redacted
    assert FAKE_SAB_API_KEY not in redacted
    assert FAKE_CAPTURE_PASSWORD not in redacted
    assert "indexer-secret" not in redacted


def test_redact_query_and_userinfo() -> None:
    redacted = redact_text(f"GET {FAKE_INDEXER_URL} and https://user:password@example.invalid/path")
    assert "indexer-secret" not in redacted
    assert "password@example" not in redacted
    assert "[REDACTED]" in redacted


def test_json_formatter_redacts_message_and_extras() -> None:
    formatter = JsonFormatter()
    output = _format(
        formatter,
        f"token={FAKE_SAB_API_KEY} nzb={FAKE_NZB_BASE64}",
        component="test",
        sequence=7,
    )
    parsed = json.loads(output)
    assert FAKE_SAB_API_KEY not in output
    assert FAKE_NZB_BASE64 not in output
    assert parsed["component"] == "test"
    assert parsed["sequence"] == 7


def test_human_formatter_redacts() -> None:
    formatter = HumanFormatter()
    output = _format(
        formatter,
        f"password={FAKE_CAPTURE_PASSWORD} apikey={FAKE_SAB_API_KEY}",
    )
    assert "capture-password-not-real" not in output
    assert "sab-api-key-not-real" not in output


def test_unknown_extras_are_not_emitted() -> None:
    formatter = JsonFormatter()
    output = _format(formatter, "hello", secret_extra=FAKE_SAB_API_KEY)
    assert FAKE_SAB_API_KEY not in output
    assert "secret_extra" not in output


def test_uvicorn_access_logger_is_disabled() -> None:
    configure_logging("INFO", "json")
    access_logger = logging.getLogger("uvicorn.access")
    assert access_logger.disabled is True
    assert access_logger.propagate is False
    assert not access_logger.hasHandlers()


def test_configured_handler_redacts() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("redaction-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info("apikey=%s", FAKE_SAB_API_KEY)
    assert FAKE_SAB_API_KEY not in stream.getvalue()
