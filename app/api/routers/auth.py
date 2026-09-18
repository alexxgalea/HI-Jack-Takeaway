from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, LoginForm, credentials_exception
from app.core.security import create_access_token, hash_password, verify_password
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.token import Token
from app.schemas.user import UserCreate, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: DbSession) -> User:
    if db.scalar(select(User).where(User.email == payload.email)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password.get_secret_value()),
        full_name=payload.full_name,
        # Registration never grants admin; roles change only through M6.
        role=UserRole.user,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(form_data: LoginForm, db: DbSession) -> Token:
    # `username` is the email - the field name is fixed by OAuth2PasswordRequestForm.
    user = db.scalar(select(User).where(User.email == form_data.username))
    # Unknown email, wrong password and deactivated account all end here, with
    # one identical response, so the endpoint cannot be used to enumerate users.
    if (
        user is None
        or not verify_password(form_data.password, user.hashed_password)
        or not user.is_active
    ):
        raise credentials_exception()

    return Token(access_token=create_access_token(subject=str(user.id), role=user.role))


@router.get("/me", response_model=UserOut)
def read_me(user: CurrentUser) -> User:
    return user
