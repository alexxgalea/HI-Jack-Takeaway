from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import OrderStatus


class OrderItemIn(BaseModel):
    restaurant_item_id: int
    # The service re-checks this. The schema stops a bad quantity at the HTTP
    # boundary (422); the service check is what protects a direct call.
    quantity: int = Field(gt=0)


class OrderCreate(BaseModel):
    restaurant_id: int
    delivery_address: str = Field(min_length=1, max_length=255)
    # An order with no lines has nothing to deliver and a total of zero.
    items: list[OrderItemIn] = Field(min_length=1)


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    restaurant_item_id: int
    quantity: int
    # The price paid, snapshotted when the order was placed - not today's menu
    # price of `restaurant_item_id`.
    unit_price: Decimal


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    restaurant_id: int
    status: OrderStatus
    delivery_address: str
    total_amount: Decimal
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemOut]


class OrderStatusUpdate(BaseModel):
    # The whole body of `PATCH /orders/{id}/status`. A value outside the enum
    # is a 422 at the boundary; a value inside it that the order cannot reach
    # from where it stands is a 409 from `assert_transition`.
    status: OrderStatus
