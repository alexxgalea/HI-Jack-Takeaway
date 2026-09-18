from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.errors import CredentialsError
from app.models.enums import UserRole
from app.schemas.token import TokenPayload

password_hash = PasswordHash.recommended()


def hash_password(plain: str) -> str:
    return password_hash.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return password_hash.verify(plain, hashed)


def create_access_token(
    subject: str,
    role: UserRole,
    expires_delta: timedelta | None = None,
) -> str:
    settings = get_settings()
    issued_at = datetime.now(UTC)
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.access_token_expire_minutes)
    expires_at = issued_at + expires_delta
    claims = {
        "sub": subject,
        "role": role.value,
        "exp": int(expires_at.timestamp()),
        "iat": int(issued_at.timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> TokenPayload:
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            # Explicit allowlist, hard-coded to HS256 rather than read from
            # settings: a token declaring `alg: none` or HS512 must be rejected
            # even if the deployment's JWT_ALGORITHM were ever changed.
            algorithms=["HS256"],
        )
        return TokenPayload(**claims)
    except (
        jwt.ExpiredSignatureError,
        jwt.InvalidTokenError,
        ValidationError,
    ) as exc:
        # One error for every failure mode - expiry, bad signature, rejected
        # algorithm, missing claims - so nothing leaks about which check failed.
        raise CredentialsError from exc
