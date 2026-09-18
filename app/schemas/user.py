from pydantic import BaseModel, ConfigDict, EmailStr, SecretStr, field_validator

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


class UserAdminUpdate(BaseModel):
    """The body of `PATCH /admin/users/{id}`.

    Only the two fields the plan puts in an admin's hands. Email, name and
    password are absent deliberately: this route promotes, demotes, suspends and
    reinstates accounts - it is not an account editor, and it must never become
    a way to take one over by rewriting its address.

    Both optional, like every other PATCH here, so `model_dump(exclude_unset=True)`
    can tell an omitted field from a sent one and leave the rest of the row alone.
    """

    role: UserRole | None = None
    is_active: bool | None = None

    @field_validator("role", "is_active")
    @classmethod
    def reject_an_explicit_null(cls, value: object) -> object:
        """`None` is how absence is spelled here, so it cannot also be a value.

        `role` and `is_active` are both NOT NULL columns. Without this, sending
        `{"role": null}` sets the field rather than leaving it unset, and
        `exclude_unset=True` faithfully carries the null down to an UPDATE that
        the database refuses - a 500 for what is plainly bad input.

        Only an explicitly sent null reaches here: Pydantic does not validate
        defaults, so an omitted field keeps its `None` and stays excluded.
        """
        if value is None:
            raise ValueError("must not be null; omit the field to leave it unchanged")
        return value
