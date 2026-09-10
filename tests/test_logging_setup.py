import json
import logging

from monitoring.logging_setup import JsonFormatter, setup_logging


def test_json_formatter_produces_valid_json_with_expected_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="codex.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello %s", args=("world",), exc_info=None,
    )
    parsed = json.loads(formatter.format(record))
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "codex.test"
    assert parsed["message"] == "hello world"
    assert "timestamp" in parsed


def test_json_formatter_includes_exception_when_present():
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = logging.LogRecord(
            name="codex.test", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="failed", args=(), exc_info=sys.exc_info(),
        )
    parsed = json.loads(formatter.format(record))
    assert "boom" in parsed["exception"]


def test_setup_logging_is_idempotent():
    setup_logging()
    first_handler_count = len(logging.getLogger().handlers)
    setup_logging()
    second_handler_count = len(logging.getLogger().handlers)
    assert first_handler_count == second_handler_count == 1
