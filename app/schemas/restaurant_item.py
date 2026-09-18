from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    # Bounds mirror the `Numeric(10, 2)` column: anything the schema accepts
    # must survive the round trip to Postgres unrounded.
    price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    is_available: bool = True


class ItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    price: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=2)
    is_available: bool | None = None

    @field_validator("name", "price", "is_available")
    @classmethod
    def reject_an_explicit_null(cls, value: object) -> object:
        """`None` is how absence is spelled here, so it cannot also be a value.

        `name`, `price` and `is_available` are NOT NULL columns. Without this,
        sending `{"price": null}` sets the field rather than leaving it unset,
        and `exclude_unset=True` faithfully carries the null down to an UPDATE
        that the database refuses - a 500 for what is plainly bad input.

        `description` is absent from the list on purpose: its column is
        nullable, so an explicit null there is a real value that clears the text.

        Only an explicitly sent null reaches here: Pydantic does not validate
        defaults, so an omitted field keeps its `None` and stays excluded.
        """
        if value is None:
            raise ValueError("must not be null; omit the field to leave it unchanged")
        return value


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    restaurant_id: int
    name: str
    description: str | None
    price: Decimal
    is_available: bool
