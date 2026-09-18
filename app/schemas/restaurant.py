from pydantic import BaseModel, ConfigDict, Field


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


class RestaurantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    address: str
    phone: str | None
    is_active: bool
