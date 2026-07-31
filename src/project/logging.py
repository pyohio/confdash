"""structlog configuration.

Keyword-style events throughout: `logger.info("program.synced", event_slug=..., talks=12)`.

The redaction processor is the important part. This app stores other organizations' provider
credentials, so a stray `logger.info("...", connection=conn)` must not put an API token in a log
aggregator. Redaction happens in the logging pipeline rather than at each call site, because
relying on every call site to remember is how tokens end up in logs.
"""

from typing import Any

import structlog

SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "api_token",
        "access_token",
        "authorization",
        "client_secret",
        "credentials",
        "field_encryption_key",
        "password",
        "refresh_token",
        "secret",
        "secret_key",
        "token",
        "token_hash",
    }
)

REDACTED = "[redacted]"


def redact_sensitive(_logger: Any, _method: str, event_dict: dict) -> dict:
    """Replace values whose key looks sensitive, at any nesting depth."""

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: (REDACTED if k.lower() in SENSITIVE_KEYS else scrub(v)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(scrub(v) for v in value)
        return value

    return scrub(event_dict)


def configure_structlog(*, log_level: str, log_format: str) -> dict:
    """Configure structlog and return a matching Django LOGGING dict."""
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        redact_sensitive,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer = (
        structlog.processors.JSONRenderer() if log_format == "json" else structlog.dev.ConsoleRenderer(colors=True)
    )

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structured": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": [
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    renderer,
                ],
                "foreign_pre_chain": shared_processors,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "structured",
            },
        },
        "root": {"handlers": ["console"], "level": log_level},
        "loggers": {
            "django": {"handlers": ["console"], "level": log_level, "propagate": False},
            # Request logging is owned by the app, not the WSGI server.
            "django.server": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        },
    }
