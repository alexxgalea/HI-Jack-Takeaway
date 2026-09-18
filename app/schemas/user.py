from pydantic import BaseModel, ConfigDict, EmailStr, SecretStr

from app.models.enums import UserRole


class UserCreate(BaseModel):
    email: EmailStr
    password: SecretStr
    full_name: str


class UserOut(BaseModel):
    # No hashed_password field here, and never one: this schema is the only
    # shape a user is ever serialised into.
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
