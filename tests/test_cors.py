"""CORS: open by default, narrowable per environment, credentials off."""

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import app as real_app

ORIGIN = "https://app.example.com"


def cors_kwargs() -> dict[str, object]:
    (cors,) = [m for m in real_app.user_middleware if m.cls is CORSMiddleware]
    return dict(cors.kwargs)


@pytest.fixture
async def narrowed_client() -> AsyncGenerator[AsyncClient, None]:
    """An app configured the way a deployment would configure it.

    The real app is built once at import with the permissive default, so the
    "only these origins" half of the setting is checked on a throwaway app
    wired the same way.
    """
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[ORIGIN],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --- the default ----------------------------------------------------------


def test_origins_default_to_open_and_come_from_settings() -> None:
    assert get_settings().cors_origins == ["*"]
    assert cors_kwargs()["allow_origins"] == get_settings().cors_origins


def test_credentials_are_disabled() -> None:
    # What makes a wildcard origin defensible: no cookie is ever sent
    # cross-origin, and the browser would reject `*` with credentials anyway.
    assert cors_kwargs()["allow_credentials"] is False


async def test_a_cross_origin_request_is_allowed(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"Origin": ORIGIN})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers


async def test_a_preflight_is_answered_without_reaching_the_endpoint(
    client: AsyncClient,
) -> None:
    response = await client.options(
        "/orders",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    # 200 from the middleware itself: `POST /orders` needs a token, and a
    # preflight carries none - if it were routed, the browser would see a 401
    # and report a CORS failure for a perfectly valid request.
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


# --- a narrowed list ------------------------------------------------------


async def test_a_configured_origin_is_allowed(narrowed_client: AsyncClient) -> None:
    response = await narrowed_client.get("/health", headers={"Origin": ORIGIN})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN


async def test_an_unconfigured_origin_gets_no_allow_header(
    narrowed_client: AsyncClient,
) -> None:
    response = await narrowed_client.get("/health", headers={"Origin": "https://evil.example.com"})

    # The response still arrives - CORS is enforced by the browser, which sees
    # no allow header and refuses to hand the body to the calling page.
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
