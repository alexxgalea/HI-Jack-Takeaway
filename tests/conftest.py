import os
from collections.abc import AsyncGenerator, Generator
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

# --- test database safety -------------------------------------------------
# These two statements run before anything below imports `app`, and they have
# to: `Settings` is cached on first use and `app.db.session` builds its engine
# at import time, so by the time a fixture runs the database is already chosen.

# The throwaway Postgres from docker-compose.test.yml. Injected into the
# environment, where it outranks `.env` - so `pytest` with nothing configured
# cannot fall through to whatever database `.env` happens to name, which is the
# same file that documents pointing DATABASE_URL at the dev database.
DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://postgres:postgres@localhost:55432/hijack_takeaway_test"
)

# M0's database, still allowed because the suite has always been run there and
# `docker compose up` is the only Postgres some checkouts have.
LEGACY_TEST_DATABASE = "hijack_takeaway"


def assert_is_a_test_database(url: str) -> None:
    """Refuse to run the suite against anything but a test database.

    The plan's M7 criterion is that running the suite never touches dev data.
    A note in a README is not what makes that true - this is. The suite writes
    real rows: `db_session` rolls its transaction back, but a killed run or a
    fixture that commits outside it does not, and M5 already lost an afternoon
    to the suite colliding with a seeded database.

    Fail closed, and by name: a database called `*_test`, or M0's
    `hijack_takeaway`, is allowed through. Everything else - `..._dev`, a
    staging URL, a production one - stops collection before a single
    connection is opened.
    """
    name = make_url(url).database
    if name is not None and (name.endswith("_test") or name == LEGACY_TEST_DATABASE):
        return
    raise RuntimeError(
        f"Refusing to run the test suite against database {name!r}: it is not a "
        f"test database. Allowed: any name ending in '_test', or "
        f"{LEGACY_TEST_DATABASE!r}.\n"
        "Start the throwaway Postgres and let the suite find it by itself:\n"
        "  docker compose -f docker-compose.test.yml up -d --wait\n"
        f"  DATABASE_URL={DEFAULT_TEST_DATABASE_URL} alembic upgrade head\n"
        "  pytest        # with DATABASE_URL unset"
    )


os.environ.setdefault("DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
assert_is_a_test_database(os.environ["DATABASE_URL"])

from app.core.security import create_access_token, hash_password  # noqa: E402
from app.db.session import engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.user import User  # noqa: E402

USER_PASSWORD = "user-password"
ADMIN_PASSWORD = "admin-password"


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """A session whose every write is rolled back when the test ends.

    The session joins an outer transaction on a single connection and turns its
    own `commit()` calls into savepoints, so code under test (which does commit)
    still sees its writes while the database is left untouched afterwards.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
async def client(db_session: Session) -> AsyncGenerator[AsyncClient, None]:
    app.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


def _create_user(db: Session, email: str, password: str, role: UserRole) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=f"{role.value.title()} Fixture",
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def user(db_session: Session) -> User:
    return _create_user(db_session, "user@example.com", USER_PASSWORD, UserRole.user)


@pytest.fixture
def admin(db_session: Session) -> User:
    return _create_user(db_session, "admin@example.com", ADMIN_PASSWORD, UserRole.admin)


@pytest.fixture
def user_token(user: User) -> str:
    return create_access_token(subject=str(user.id), role=user.role)


@pytest.fixture
def admin_token(admin: User) -> str:
    return create_access_token(subject=str(admin.id), role=admin.role)


@pytest.fixture
def expired_token(user: User) -> str:
    return create_access_token(
        subject=str(user.id),
        role=user.role,
        expires_delta=timedelta(minutes=-1),
    )
