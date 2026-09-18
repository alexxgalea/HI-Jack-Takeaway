"""Structured logging: JSON lines, with secrets scrubbed on the way out.

Two pieces, both small on purpose. `JsonFormatter` renders one JSON object per
record so a log shipper can read the fields instead of a regex over prose.
`RedactingFilter` is the safety net: nothing here logs a request body or a
header, so a token should never reach a record in the first place - but "should
never" is not a guarantee, and a credential written to a log outlives the
request that leaked it. The filter runs on the handler, so it scrubs whatever
any module in the process hands to logging, not just the calls in this file.

No third-party logging dependency, no correlation ids, no shipping. M7 asks for
structured output and redaction; that is what this is.
"""

import json
import logging
import re
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REDACTED = "[REDACTED]"

# Matched case-insensitively against the *key*, so `Authorization`,
# `authorization` and an `extra={"password": ...}` all land the same way.
SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "password",
        "hashed_password",
        "new_password",
        "current_password",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "secret",
        "jwt_secret",
    }
)

# The second line of defence: a credential pasted into a message string has no
# key to match on. These catch the two shapes that actually occur - an
# `Authorization: Bearer <jwt>` header echoed into text, and a sensitive field
# written as `password=...` or `"password": "..."`.
_BEARER = re.compile(r"(?i)\b(bearer)\s+[\w\-._~+/]+=*")
_KEYED_VALUE = re.compile(
    r"(?i)\b(" + "|".join(sorted(SENSITIVE_KEYS)) + r")\b(\"?\s*[:=]\s*)(\"[^\"]*\"|\S+)"
)

# Everything the logging module itself puts on a record; anything else was
# passed by the caller as `extra=` and belongs in the JSON output.
_RESERVED = frozenset(
    vars(
        logging.LogRecord(
            name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None
        )
    )
) | {"message", "asctime", "taskName"}


def scrub_text(text: str) -> str:
    """Blank out credential-shaped substrings in a rendered message."""
    text = _BEARER.sub(rf"\1 {REDACTED}", text)
    return _KEYED_VALUE.sub(rf"\1\2{REDACTED}", text)


def redact(value: Any) -> Any:
    """Copy `value`, replacing anything filed under a sensitive key.

    Recurses through dicts and lists so a nested `{"headers": {"Authorization":
    ...}}` is caught as readily as a top-level one. Strings are scrubbed for
    credential-shaped text even when their key is innocuous.
    """
    if isinstance(value, dict):
        return {
            key: REDACTED
            if isinstance(key, str) and key.lower() in SENSITIVE_KEYS
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(redact(item) for item in value)
    if isinstance(value, str):
        return scrub_text(value)
    return value


class RedactingFilter(logging.Filter):
    """Scrub a record in place before a handler gets to format it.

    Attached to the handler rather than to a logger: a filter on a logger is
    skipped by records that propagate up from its children, which would leave
    every other module in the process unprotected.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub_text(record.msg)
        if record.args:
            record.args = redact(record.args)
        for key in set(vars(record)) - _RESERVED:
            value = getattr(record, key)
            if key.lower() in SENSITIVE_KEYS:
                setattr(record, key, REDACTED)
            else:
                setattr(record, key, redact(value))
        return True  # a filter that never drops a record, only rewrites it


class JsonFormatter(logging.Formatter):
    """One JSON object per line: the standard fields plus any `extra=`."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(
            {key: getattr(record, key) for key in set(vars(record)) - _RESERVED}
        )
        if record.exc_info:
            # The traceback belongs in the log and nowhere else - the response
            # for the same failure is a bare "Internal server error".
            payload["exception"] = scrub_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Install the JSON handler on the root logger.

    Idempotent, and additive: it replaces only a handler it installed itself,
    so a caller that has already attached its own (pytest's capture, say) keeps
    it. Called from the app factory, which is what makes the app's own records
    structured without asking uvicorn to reconfigure anything.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    handler.set_name("hijack-json")

    root = logging.getLogger()
    for existing in [h for h in root.handlers if h.get_name() == "hijack-json"]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log one line per request: what was asked, how it ended, how long it took.

    Metadata only. No headers and no body - not even redacted ones, because the
    cheapest way to never log a password is to never read one into a record.
    The path is logged without the query string for the same reason: a token
    handed to an endpoint as a query parameter would otherwise be written down
    verbatim.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        logger = logging.getLogger("app.request")
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Logged here so the timing survives, then re-raised for the
            # handler that owns the 500 response.
            self._log(logger, request, 500, started)
            raise
        self._log(logger, request, response.status_code, started)
        return response

    @staticmethod
    def _log(
        logger: logging.Logger, request: Request, status_code: int, started: float
    ) -> None:
        logger.info(
            "%s %s %s",
            request.method,
            request.url.path,
            status_code,
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
