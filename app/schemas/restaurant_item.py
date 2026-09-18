from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


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


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    restaurant_id: int
    name: str
    description: str | None
    price: Decimal
    is_available: bool
