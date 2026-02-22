"""Tests for logging redaction helpers in main module."""

import logging

from src.main import _SensitiveLogFilter, _redact_telegram_bot_token


def test_redact_telegram_bot_token_in_url() -> None:
    raw = (
        "HTTP Request: POST "
        'https://api.telegram.org/bot123456:ABCdefGHI_jkl-9876543210/getUpdates "HTTP/1.1 200 OK"'
    )
    result = _redact_telegram_bot_token(raw)

    assert "bot***REDACTED***" in result
    assert "123456:ABCdefGHI_jkl-9876543210" not in result


def test_sensitive_filter_redacts_log_record_message() -> None:
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=("HTTP Request: POST %s"),
        args=(
            "https://api.telegram.org/bot123456:ABCdefGHI_jkl-9876543210/getUpdates",
        ),
        exc_info=None,
    )

    filtered = _SensitiveLogFilter().filter(record)

    assert filtered is True
    assert "bot***REDACTED***" in record.msg
    assert "123456:ABCdefGHI_jkl-9876543210" not in record.msg
    assert record.args == ()
