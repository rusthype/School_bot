"""
Logging configuration for the bot.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Dict

LOG_DIR = Path(__file__).resolve().parents[3] / "logs"
ARCHIVE_DIR = LOG_DIR / "archive"


class SensitiveFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage().lower()
        if "password" in message:
            return False
        return True


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "user_id"):
            record.user_id = "-"
        if not hasattr(record, "chat_id"):
            record.chat_id = "-"
        if not hasattr(record, "command"):
            record.command = "-"
        if not hasattr(record, "exec_ms"):
            record.exec_ms = "-"
        return True


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[41m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if sys.stdout.isatty():
            color = self.COLORS.get(record.levelno, "")
            if color:
                return f"{color}{message}{self.RESET}"
        return message


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "user_id": getattr(record, "user_id", None),
            "chat_id": getattr(record, "chat_id", None),
            "command": getattr(record, "command", None),
            "exec_ms": getattr(record, "exec_ms", None),
            "function": record.funcName,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(app_name: str = "school_bot") -> logging.Logger:
    """Setup logging configuration"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(app_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if logger.handlers:
        return logger

    base_format = (
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s "
        "[user_id=%(user_id)s chat_id=%(chat_id)s command=%(command)s exec_ms=%(exec_ms)s func=%(funcName)s]"
    )

    context_filter = ContextFilter()
    sensitive_filter = SensitiveFilter()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(ColorFormatter(base_format))
    console_handler.addFilter(context_filter)
    console_handler.addFilter(sensitive_filter)

    file_handler = TimedRotatingFileHandler(
        LOG_DIR / f"{app_name}.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(base_format))
    file_handler.addFilter(context_filter)
    file_handler.addFilter(sensitive_filter)

    error_handler = TimedRotatingFileHandler(
        LOG_DIR / f"{app_name}.error.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    error_handler.suffix = "%Y-%m-%d"
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(logging.Formatter(base_format))
    error_handler.addFilter(context_filter)
    error_handler.addFilter(sensitive_filter)

    debug_handler = TimedRotatingFileHandler(
        LOG_DIR / f"{app_name}.debug.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    debug_handler.suffix = "%Y-%m-%d"
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(logging.Formatter(base_format))
    debug_handler.addFilter(context_filter)
    debug_handler.addFilter(sensitive_filter)

    archive_handler = TimedRotatingFileHandler(
        ARCHIVE_DIR / f"{app_name}.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    archive_handler.suffix = "%Y-%m-%d"
    archive_handler.setLevel(logging.INFO)
    archive_handler.setFormatter(logging.Formatter(base_format))
    archive_handler.addFilter(context_filter)
    archive_handler.addFilter(sensitive_filter)

    json_handler = TimedRotatingFileHandler(
        LOG_DIR / f"{app_name}.json.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    json_handler.suffix = "%Y-%m-%d"
    json_handler.setLevel(logging.INFO)
    json_handler.setFormatter(JsonFormatter())
    json_handler.addFilter(context_filter)
    json_handler.addFilter(sensitive_filter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.addHandler(error_handler)
    logger.addHandler(debug_handler)
    logger.addHandler(archive_handler)
    logger.addHandler(json_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance.

    Calls setup_logging() the first time it's invoked, so the parent
    'school_bot' logger always has the file handlers attached before
    any child logger tries to propagate up. Without this auto-init the
    /logs panel saw empty files because no record ever reached disk —
    the parent had no handlers, child loggers propagated to it, and
    the records were silently dropped.

    setup_logging() is itself idempotent (early-returns if handlers
    already exist), so multiple calls from different importers are
    cheap.
    """
    if not _root_initialized():
        setup_logging()
    return logging.getLogger(name)


def _root_initialized() -> bool:
    """True if the 'school_bot' logger already has its file handlers.

    Used by get_logger() to decide whether to call setup_logging().
    Checking by name avoids re-running setup for unrelated loggers.
    """
    return bool(logging.getLogger("school_bot").handlers)
