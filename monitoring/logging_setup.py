"""Structured (JSON-lines) logging - Section 68 of the master spec.
Deliberately never logs secrets: callers are responsible for not passing
credential/token values into log messages, and this module's own setup
code never touches config.settings.Settings' credential fields at all
(no accidental repr()/str() of the whole settings object anywhere here -
see test_logging_setup.py's own check for this).
"""
import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(level: str = "INFO") -> None:
    """Idempotent - safe to call more than once (e.g. once from app/main.py
    and again from a test) without stacking duplicate handlers."""
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
