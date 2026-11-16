"""Structured JSON logging and sensitive data redaction for Demerivo PixelFix."""

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict

SENSITIVE_PATTERNS = [
    re.compile(r'(?i)\b(password|secret|token|api[_-]?key|authorization|bearer)\s*[:=]\s*([\'"]?[^\s,;\'"]+[\'"]?)'),
    re.compile(r'\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b'),
]


def redact_sensitive_string(text: str) -> str:
    """Redact passwords, secret tokens, API keys, and JWT signatures from log strings."""
    if not text or not isinstance(text, str):
        return text

    for pattern in SENSITIVE_PATTERNS:
        def repl(match):
            if match.re.groups >= 2:
                key = match.group(1)
                return f"{key}=[REDACTED]"
            return "[REDACTED]"
        text = pattern.sub(repl, text)
    return text


class SafeJsonLogFormatter(logging.Formatter):
    """Outputs machine-parseable structured JSON logs with sensitive credential redaction."""

    REDACTED_KEYS = {
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "authorization",
        "key_secret",
        "jwt",
    }

    def format(self, record: logging.LogRecord) -> str:
        raw_message = record.getMessage()
        safe_message = redact_sensitive_string(raw_message)

        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": safe_message,
            "module": record.module,
            "line": record.lineno,
        }

        # Attach request_id if present in extra or context
        if hasattr(record, "request_id"):
            log_entry["request_id"] = record.request_id

        # Attach exception details if present
        if record.exc_info:
            log_entry["exception"] = redact_sensitive_string(self.formatException(record.exc_info))

        return json.dumps(log_entry)


def setup_logging(level: str = "INFO", json_format: bool = False):
    """Configure root logger with appropriate formatting."""
    root_logger = logging.getLogger()
    log_level = getattr(logging, level.upper(), logging.INFO)
    root_logger.setLevel(log_level)

    # Clear existing handlers
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if json_format:
        handler.setFormatter(SafeJsonLogFormatter())
    else:
        fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
        handler.setFormatter(fmt)

    root_logger.addHandler(handler)
