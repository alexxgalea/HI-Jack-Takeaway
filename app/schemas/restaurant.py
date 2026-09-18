from pydantic import BaseModel, ConfigDict, Field, field_validator


class RestaurantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    address: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    is_active: bool = True


class RestaurantUpdate(BaseModel):
    # Every field optional, because PATCH applies only what the caller sent.
    # `model_dump(exclude_unset=True)` is what separates "absent" from an
    # explicit null, so omitting `phone` leaves it alone while sending
    # `"phone": null` clears it.
    name: str | None = Field(default=None, min_length=1, max_length=255)
    address: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    is_active: bool | None = None

    @field_validator("name", "address", "is_active")
    @classmethod
    def reject_an_explicit_null(cls, value: object) -> object:
        """`None` is how absence is spelled here, so it cannot also be a value.

        `name`, `address` and `is_active` are NOT NULL columns. Without this,
        sending `{"name": null}` sets the field rather than leaving it unset,
        and `exclude_unset=True` faithfully carries the null down to an UPDATE
        that the database refuses - a 500 for what is plainly bad input.

        `phone` is absent from the list on purpose: its column is nullable, so
        an explicit null there is a real value that clears the number.

        Only an explicitly sent null reaches here: Pydantic does not validate
        defaults, so an omitted field keeps its `None` and stays excluded.
        """
        if value is None:
            raise ValueError("must not be null; omit the field to leave it unchanged")
        return value


class RestaurantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    address: str
    phone: str | None
    is_active: bool
