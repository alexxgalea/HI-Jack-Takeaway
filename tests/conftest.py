from collections.abc import AsyncGenerator, Generator
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.db.session import engine, get_db
from app.main import app
from app.models.enums import UserRole
from app.models.user import User

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
