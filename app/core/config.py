from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("jwt_secret must be set to a non-empty value")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
