from datetime import timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.errors import CredentialsError
from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.enums import UserRole


def _claims(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "sub": "1",
        "role": UserRole.user.value,
        "exp": 9_999_999_999,
        "iat": 1_700_000_000,
    }
    base.update(overrides)
    return base


def test_hash_password_is_not_the_plaintext() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert "correct horse battery staple" not in hashed


def test_hashing_the_same_password_twice_gives_different_hashes() -> None:
    first = hash_password("same-input")
    second = hash_password("same-input")
    assert first != second
    assert verify_password("same-input", first)
    assert verify_password("same-input", second)


def test_verify_password_rejects_the_wrong_password() -> None:
    assert not verify_password("wrong", hash_password("right"))


def test_token_roundtrip_carries_sub_role_exp_and_iat() -> None:
    token = create_access_token(subject="42", role=UserRole.admin)
    payload = decode_token(token)
    assert payload.sub == "42"
    assert payload.role is UserRole.admin
    assert payload.exp > payload.iat


def test_token_lifetime_comes_from_settings_and_sits_in_the_15_to_30_minute_band() -> None:
    settings = get_settings()
    payload = decode_token(create_access_token(subject="1", role=UserRole.user))
    lifetime_minutes = (payload.exp - payload.iat) / 60
    assert lifetime_minutes == pytest.approx(settings.access_token_expire_minutes, abs=1)
    assert 15 <= lifetime_minutes <= 30


def test_expired_token_is_rejected() -> None:
    token = create_access_token(
        subject="1", role=UserRole.user, expires_delta=timedelta(minutes=-1)
    )
    with pytest.raises(CredentialsError):
        decode_token(token)


def test_token_signed_with_another_secret_is_rejected() -> None:
    token = jwt.encode(
        _claims(), "a-completely-different-secret-of-sufficient-length", algorithm="HS256"
    )
    with pytest.raises(CredentialsError):
        decode_token(token)


@pytest.mark.parametrize(
    ("algorithm", "key"),
    [("none", None), ("HS512", get_settings().jwt_secret)],
)
def test_tokens_outside_the_algorithm_allowlist_are_rejected(
    algorithm: str, key: str | None
) -> None:
    token = jwt.encode(_claims(), key, algorithm=algorithm)
    with pytest.raises(CredentialsError):
        decode_token(token)


def test_malformed_token_is_rejected() -> None:
    with pytest.raises(CredentialsError):
        decode_token("not-a-jwt")


def test_token_missing_required_claims_is_rejected() -> None:
    token = jwt.encode({"sub": "1"}, get_settings().jwt_secret, algorithm="HS256")
    with pytest.raises(CredentialsError):
        decode_token(token)
