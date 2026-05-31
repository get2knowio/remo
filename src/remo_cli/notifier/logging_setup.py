"""structlog configuration for the notifier, with secret-safe redaction.

INFO and above carry only structural metadata (approval_id, decision,
latency_ms, transport, pending_count, ...). Sensitive values — the bot token,
raw request bodies, and workspace paths — are stripped from any event that is
not emitted at DEBUG level (FR-017, finding G1/SC-006).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

# Event keys that must never appear at INFO or above. Kept deliberately broad:
# anything carrying a secret or a raw/unbounded payload belongs here.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "bot_token",
        "token",
        "authorization",
        "secret",
        "request_body",
        "raw_body",
        "body",
        "workspace",
        "policy_message",
    }
)

_REDACTED = "[redacted]"


def _redact_sensitive(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Drop sensitive keys unless the event is logged at DEBUG level.

    ``method_name`` is the bound log method ("debug", "info", ...). At DEBUG we
    keep everything (developer opt-in); at every higher level the sensitive keys
    are removed entirely so they cannot leak into journald.
    """
    if method_name == "debug":
        return event_dict
    for key in SENSITIVE_KEYS:
        if key in event_dict:
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(level: str = "info", *, json_logs: bool | None = None) -> None:
    """Configure structlog + stdlib logging for the notifier process.

    Args:
        level: One of debug | info | warning | error.
        json_logs: Force JSON rendering (True) or key-value rendering (False).
            When None, JSON is used unless stdout is a TTY (so containers emit
            JSON to journald while local dev stays human-readable).
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    if json_logs is None:
        json_logs = not sys.stdout.isatty()

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=numeric_level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_sensitive,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "remo_notifier") -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
