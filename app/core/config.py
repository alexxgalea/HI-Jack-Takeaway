from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # Browser origins allowed to call the API. Wide open by default because a
    # deployment's real origins are not knowable here and a dev frontend on
    # any port has to work out of the box; narrow it per environment by
    # setting CORS_ORIGINS to a JSON list, e.g. ["https://app.example.com"].
    # Safe to leave open only because credentials are off (see main.py): the
    # API authenticates with a Bearer header the client sets by hand, never
    # with a cookie the browser would attach on its own.
    cors_origins: list[str] = ["*"]

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("jwt_secret must be set to a non-empty value")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
