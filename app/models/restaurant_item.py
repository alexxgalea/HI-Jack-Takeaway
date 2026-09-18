from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.order_item import OrderItem
    from app.models.restaurant import Restaurant


class RestaurantItem(Base):
    __tablename__ = "restaurant_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    is_available: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    restaurant: Mapped["Restaurant"] = relationship(back_populates="items")
    order_items: Mapped[list["OrderItem"]] = relationship(
        back_populates="restaurant_item"
    )
