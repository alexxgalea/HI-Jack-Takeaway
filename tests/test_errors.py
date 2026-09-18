"""The AppError hierarchy and the handlers that render it.

The mapping from error to status code is exercised twice over: here against a
throwaway app, so a handler can be checked without an endpoint in the way, and
implicitly by every M4/M5 test that asserts a 400, 404 or 409 from a service
that now raises these instead of `HTTPException`.
"""

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.errors import (
    AppError,
    BusinessRuleError,
    ConflictError,
    CredentialsError,
    NotFoundError,
    register_exception_handlers,
)
from app.main import app as real_app

# The whole hierarchy, with the answer each member owes.
ERRORS = [
    (CredentialsError, 401, "Could not validate credentials"),
    (NotFoundError, 404, "Not found"),
    (BusinessRuleError, 400, "Request rejected"),
    (ConflictError, 409, "Conflict"),
]
BY_NAME = {cls.__name__: cls for cls, _, _ in ERRORS}

# A value that must never reach a response body, only the log.
CRASH_SECRET = "hunter2-in-the-traceback"


@pytest.fixture
def handler_app() -> FastAPI:
    """A bare app with M7's handlers and nothing else on it."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise/{name}")
    def raise_by_name(name: str) -> None:
        raise BY_NAME[name]()

    @app.get("/raise-with-detail")
    def raise_with_detail() -> None:
        raise BusinessRuleError("Item 7 is not available")

    @app.get("/crash")
    def crash() -> None:
        raise RuntimeError(CRASH_SECRET)

    return app


@pytest.fixture
async def handler_client(handler_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    # `raise_app_exceptions=False` so the transport returns the 500 the handler
    # built instead of re-raising into the test, which is what a real client on
    # the other end of the socket would see.
    transport = ASGITransport(app=handler_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --- the hierarchy --------------------------------------------------------


@pytest.mark.parametrize(("error", "status_code", "detail"), ERRORS)
def test_each_error_carries_its_status_and_detail(
    error: type[AppError], status_code: int, detail: str
) -> None:
    assert issubclass(error, AppError)
    assert error.status_code == status_code
    assert error.detail == detail


def test_a_detail_passed_in_overrides_the_default() -> None:
    error = BusinessRuleError("Item 7 is not available")
    assert error.detail == "Item 7 is not available"
    # `str(exc)` and the response body must not be able to disagree.
    assert str(error) == "Item 7 is not available"


def test_credentials_error_is_the_only_one_carrying_headers() -> None:
    assert CredentialsError.headers == {"WWW-Authenticate": "Bearer"}
    assert [cls for cls, _, _ in ERRORS if cls.headers is not None] == [
        CredentialsError
    ]


# --- the handlers ---------------------------------------------------------


@pytest.mark.parametrize(("error", "status_code", "detail"), ERRORS)
async def test_handler_answers_with_status_and_detail(
    handler_client: AsyncClient, error: type[AppError], status_code: int, detail: str
) -> None:
    response = await handler_client.get(f"/raise/{error.__name__}")
    assert response.status_code == status_code
    # The one shape every expected failure answers in.
    assert response.json() == {"detail": detail}


async def test_handler_passes_the_per_raise_detail_through(
    handler_client: AsyncClient,
) -> None:
    response = await handler_client.get("/raise-with-detail")
    assert response.status_code == 400
    assert response.json() == {"detail": "Item 7 is not available"}


async def test_credentials_error_answers_with_the_bearer_challenge(
    handler_client: AsyncClient,
) -> None:
    response = await handler_client.get("/raise/CredentialsError")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_unexpected_exception_becomes_a_generic_500(
    handler_client: AsyncClient,
) -> None:
    response = await handler_client.get("/crash")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}


async def test_a_500_body_leaks_neither_traceback_nor_message(
    handler_client: AsyncClient,
) -> None:
    response = await handler_client.get("/crash")
    body = response.text
    # The exception's own message, the traceback and the module layout all
    # belong in the log and nowhere near the caller.
    assert CRASH_SECRET not in body
    assert "Traceback" not in body
    assert "RuntimeError" not in body


# --- the real app ---------------------------------------------------------


def test_the_app_registers_both_handlers() -> None:
    """Wiring check: the factory installs them, not just the tests' fixture."""
    assert AppError in real_app.exception_handlers
    assert Exception in real_app.exception_handlers
