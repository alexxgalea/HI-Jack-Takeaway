import subprocess
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import AdminUser
from app.core.config import get_settings
from app.core.security import decode_token
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.user import UserOut
from tests.conftest import ADMIN_PASSWORD, USER_PASSWORD


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- registration ---------------------------------------------------------


async def test_register_returns_201_and_a_user_without_any_password_field(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/auth/register",
        json={
            "email": "new@example.com",
            "password": "a-good-password",
            "full_name": "New Customer",
        },
    )
    assert response.status_code == 201

    body = response.json()
    assert body["email"] == "new@example.com"
    assert body["full_name"] == "New Customer"
    assert body["role"] == UserRole.user.value
    assert body["is_active"] is True
    assert set(body) == set(UserOut.model_fields)
    assert "password" not in response.text
    assert "hashed_password" not in response.text
    assert "$argon2" not in response.text


async def test_register_always_assigns_the_user_role(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={
            "email": "wants-admin@example.com",
            "password": "a-good-password",
            "full_name": "Hopeful",
            "role": "admin",
        },
    )
    assert response.status_code == 201
    assert response.json()["role"] == UserRole.user.value


async def test_register_with_a_duplicate_email_returns_409(
    client: AsyncClient, user: User
) -> None:
    response = await client.post(
        "/auth/register",
        json={
            "email": user.email,
            "password": "another-password",
            "full_name": "Impostor",
        },
    )
    assert response.status_code == 409


async def test_register_rejects_a_malformed_email(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={"email": "not-an-email", "password": "pw", "full_name": "X"},
    )
    assert response.status_code == 422


# --- login ----------------------------------------------------------------


async def test_login_returns_a_bearer_token_whose_claims_describe_the_user(
    client: AsyncClient, user: User
) -> None:
    response = await client.post(
        "/auth/login", data={"username": user.email, "password": USER_PASSWORD}
    )
    assert response.status_code == 200

    body = response.json()
    assert set(body) == {"access_token", "token_type"}
    assert body["token_type"] == "bearer"

    payload = decode_token(body["access_token"])
    assert payload.sub == str(user.id)
    assert payload.role is UserRole.user
    assert payload.exp > payload.iat


async def test_login_token_expires_between_15_and_30_minutes_out(
    client: AsyncClient, user: User
) -> None:
    response = await client.post(
        "/auth/login", data={"username": user.email, "password": USER_PASSWORD}
    )
    payload = decode_token(response.json()["access_token"])
    lifetime_minutes = (payload.exp - payload.iat) / 60
    assert lifetime_minutes == pytest.approx(
        get_settings().access_token_expire_minutes, abs=1
    )
    assert 15 <= lifetime_minutes <= 30


async def test_login_never_reveals_whether_the_email_exists(
    client: AsyncClient, user: User
) -> None:
    wrong_password = await client.post(
        "/auth/login", data={"username": user.email, "password": "not-the-password"}
    )
    unknown_email = await client.post(
        "/auth/login", data={"username": "nobody@example.com", "password": USER_PASSWORD}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.headers["WWW-Authenticate"] == "Bearer"


async def test_login_by_a_deactivated_user_is_refused(
    client: AsyncClient, db_session: Session, user: User
) -> None:
    user.is_active = False
    db_session.commit()

    response = await client.post(
        "/auth/login", data={"username": user.email, "password": USER_PASSWORD}
    )
    assert response.status_code == 401


# --- /auth/me -------------------------------------------------------------


async def test_me_returns_the_authenticated_user(
    client: AsyncClient, user: User, user_token: str
) -> None:
    response = await client.get("/auth/me", headers=auth_header(user_token))
    assert response.status_code == 200
    assert response.json()["email"] == user.email
    assert response.json()["id"] == user.id


async def test_me_without_a_token_returns_401(client: AsyncClient) -> None:
    response = await client.get("/auth/me")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_me_with_a_malformed_token_returns_401(client: AsyncClient) -> None:
    response = await client.get("/auth/me", headers=auth_header("not-a-jwt"))
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_me_with_an_expired_token_returns_401(
    client: AsyncClient, expired_token: str
) -> None:
    response = await client.get("/auth/me", headers=auth_header(expired_token))
    assert response.status_code == 401


async def test_me_with_a_token_signed_by_another_secret_returns_401(
    client: AsyncClient, user: User
) -> None:
    forged = jwt.encode(
        {
            "sub": str(user.id),
            "role": UserRole.admin.value,
            "exp": 9_999_999_999,
            "iat": 1_700_000_000,
        },
        "a-completely-different-secret-of-sufficient-length",
        algorithm="HS256",
    )
    response = await client.get("/auth/me", headers=auth_header(forged))
    assert response.status_code == 401


@pytest.mark.parametrize("algorithm", ["none", "HS512"])
async def test_me_rejects_tokens_outside_the_algorithm_allowlist(
    client: AsyncClient, user: User, algorithm: str
) -> None:
    key = None if algorithm == "none" else get_settings().jwt_secret
    forged = jwt.encode(
        {
            "sub": str(user.id),
            "role": UserRole.admin.value,
            "exp": 9_999_999_999,
            "iat": 1_700_000_000,
        },
        key,
        algorithm=algorithm,
    )
    response = await client.get("/auth/me", headers=auth_header(forged))
    assert response.status_code == 401


async def test_me_with_a_token_for_a_deleted_user_returns_401(
    client: AsyncClient, db_session: Session, user: User, user_token: str
) -> None:
    db_session.delete(user)
    db_session.commit()

    response = await client.get("/auth/me", headers=auth_header(user_token))
    assert response.status_code == 401


async def test_a_deactivated_users_still_valid_token_returns_401(
    client: AsyncClient, db_session: Session, user: User, user_token: str
) -> None:
    assert (await client.get("/auth/me", headers=auth_header(user_token))).status_code == 200

    user.is_active = False
    db_session.commit()

    response = await client.get("/auth/me", headers=auth_header(user_token))
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


# --- require_admin --------------------------------------------------------


@pytest.fixture
async def admin_client(db_session: Session) -> AsyncClient:
    """A throwaway app with one admin-only route, to exercise `AdminUser`.

    M2 ships no admin endpoint of its own - the first ones arrive in M3 - so the
    dependency is driven here through a route that exists only for this test.
    """
    probe = FastAPI()

    @probe.get("/admin-only")
    def admin_only(admin: AdminUser) -> dict[str, str]:
        return {"email": admin.email}

    probe.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=probe)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_require_admin_rejects_a_user_token_with_403(
    admin_client: AsyncClient, user_token: str
) -> None:
    response = await admin_client.get("/admin-only", headers=auth_header(user_token))
    assert response.status_code == 403


async def test_require_admin_accepts_an_admin_token(
    admin_client: AsyncClient, admin: User, admin_token: str
) -> None:
    response = await admin_client.get("/admin-only", headers=auth_header(admin_token))
    assert response.status_code == 200
    assert response.json() == {"email": admin.email}


async def test_require_admin_still_returns_401_without_a_token(
    admin_client: AsyncClient,
) -> None:
    response = await admin_client.get("/admin-only")
    assert response.status_code == 401


# --- conventions ----------------------------------------------------------


def test_routers_use_the_type_aliases_rather_than_inline_depends() -> None:
    routers_dir = Path(__file__).resolve().parent.parent / "app" / "api" / "routers"
    result = subprocess.run(
        ["grep", "-r", "Depends(get_current_user)\\|Depends(require_admin)", str(routers_dir)],
        capture_output=True,
        text=True,
    )
    assert result.stdout == ""
    assert result.returncode == 1  # grep found nothing


# --- /docs Authorize flow -------------------------------------------------


async def test_openapi_advertises_the_password_flow_against_auth_login(
    client: AsyncClient,
) -> None:
    schema = (await client.get("/openapi.json")).json()
    flow = schema["components"]["securitySchemes"]["OAuth2PasswordBearer"]["flows"]
    assert flow["password"]["tokenUrl"] == "/auth/login"


async def test_docs_authorize_flow_works_end_to_end(
    client: AsyncClient, admin: User
) -> None:
    """What the /docs Authorize button does: form login, then a bearer call."""
    login = await client.post(
        "/auth/login",
        data={"username": admin.email, "password": ADMIN_PASSWORD},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login.status_code == 200

    token = login.json()["access_token"]
    me = await client.get("/auth/me", headers=auth_header(token))
    assert me.status_code == 200
    assert me.json()["email"] == admin.email
    assert me.json()["role"] == UserRole.admin.value
