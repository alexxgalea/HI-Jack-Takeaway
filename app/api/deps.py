from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.errors import CredentialsError
from app.core.security import decode_token
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# Aliases exist so no router signature ever spells out `Depends(...)` inline.
DbSession = Annotated[Session, Depends(get_db)]
LoginForm = Annotated[OAuth2PasswordRequestForm, Depends()]


def credentials_exception() -> HTTPException:
    """The single 401 every authentication failure raises.

    Same status, same headers, same detail whatever went wrong.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(token: Annotated[str, Depends(oauth2_scheme)], db: DbSession) -> User:
    try:
        payload = decode_token(token)
    except CredentialsError as exc:
        raise credentials_exception() from exc

    try:
        user_id = int(payload.sub)
    except ValueError as exc:
        raise credentials_exception() from exc

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise credentials_exception()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return user


AdminUser = Annotated[User, Depends(require_admin)]
