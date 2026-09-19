"""Application errors and the handlers that turn them into responses.

Services raise these instead of `HTTPException`: an ordering rule is a fact
about the domain, not about HTTP, and a service that imports `fastapi` to say
"this basket is invalid" has the web framework reaching a layer below the one
it belongs to. The mapping from error to status code lives here, in one table,
so the routers stay thin and the services stay framework-free.

Routers keep raising `HTTPException` for plumbing - a missing row, a forbidden
role. That is deliberate: FastAPI already answers those in the same
`{"detail": ...}` shape, and rewriting them would churn four milestones of
settled code for no change in behaviour.
"""

import logging
from typing import cast

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for application-level errors.

    Each subclass fixes the status code it answers with and a default detail;
    callers override the detail per raise when they have something specific to
    say. `headers` exists for the one case that needs it - the `WWW-Authenticate`
    challenge on a 401.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail: str = "Internal server error"
    headers: dict[str, str] | None = None

    def __init__(self, detail: str | None = None) -> None:
        # The instance attribute shadows the class one when given, so
        # `str(exc)` and the response body always agree.
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class CredentialsError(AppError):
    """A token could not be validated.

    Carries no detail on purpose: expired, malformed, wrongly signed and
    signed-with-the-wrong-algorithm all collapse into this one error so
    callers cannot learn which check failed.
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    detail = "Could not validate credentials"
    headers = {"WWW-Authenticate": "Bearer"}


class NotFoundError(AppError):
    """The resource the request names does not exist."""

    status_code = status.HTTP_404_NOT_FOUND
    detail = "Not found"


class BusinessRuleError(AppError):
    """A well-formed request that breaks a domain rule.

    Distinct from 422: the payload parsed fine, it just asks for something the
    domain does not allow.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    detail = "Request rejected"


class ConflictError(AppError):
    """The request conflicts with the state the resource is in right now.

    The caller is allowed to make it and it is well-formed; it is the current
    state that refuses, which is why this is 409 rather than 400.
    """

    status_code = status.HTTP_409_CONFLICT
    detail = "Conflict"


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Answer an `AppError` with its own status and `{"detail": ...}`."""
    # Starlette types every handler as taking a bare Exception; this one is
    # only ever registered for AppError.
    error = cast(AppError, exc)
    return JSONResponse(
        status_code=error.status_code,
        content={"detail": error.detail},
        headers=error.headers,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Answer anything that reaches here with a generic 500.

    The traceback is logged, never returned: a stack trace in a response body
    hands an attacker the file layout, the library versions and often the SQL.
    The caller gets the same sentence whatever broke.
    """
    logger.exception(
        "Unhandled exception",
        extra={"method": request.method, "path": request.url.path},
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire both handlers onto an app.

    A function rather than inline calls in the factory so the tests can stand
    up a throwaway app with exactly the same wiring.

    `RequestValidationError` is deliberately left to FastAPI: its 422 body is a
    list of per-field errors under `detail`, which is more useful than a single
    sentence and has been the API's answer since M0.
    """
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
