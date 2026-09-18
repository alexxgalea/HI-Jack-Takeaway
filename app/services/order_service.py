from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import OrderStatus
from app.models.order import Order
from app.models.order_item import OrderItem
from app.models.restaurant import Restaurant
from app.models.restaurant_item import RestaurantItem
from app.models.user import User
from app.schemas.order import OrderCreate


def _rejected(detail: str) -> HTTPException:
    """A well-formed request that breaks an ordering rule.

    Distinct from 422: the payload parsed fine, it just asks for something the
    menu does not allow. Item ids are echoed back because the caller already
    knows them - no information crosses that was not sent in.
    """
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def create_order(db: Session, customer: User, payload: OrderCreate) -> Order:
    """Validate a basket against the live menu and persist it as one order.

    Every rule is checked before anything is written, and the single `commit()`
    at the end is the only one: an order never lands half-built, with some
    lines saved and a later line rejected.

    Prices are copied onto the order lines here and never read back from the
    menu. That snapshot is what makes a stored total final - re-pricing a dish
    tomorrow must not silently restate what a customer was charged today.
    """
    restaurant = db.get(Restaurant, payload.restaurant_id)
    if restaurant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Restaurant not found"
        )
    if not restaurant.is_active:
        raise _rejected("Restaurant is not accepting orders")

    # One query for the whole basket, then all membership and availability
    # checks run against it in memory.
    ordered_ids = {line.restaurant_item_id for line in payload.items}
    menu = {
        item.id: item
        for item in db.scalars(
            select(RestaurantItem).where(RestaurantItem.id.in_(ordered_ids))
        )
    }

    order = Order(
        customer_id=customer.id,
        restaurant_id=restaurant.id,
        status=OrderStatus.pending,
        delivery_address=payload.delivery_address,
        total_amount=Decimal("0.00"),
    )

    total = Decimal("0.00")
    for line in payload.items:
        item = menu.get(line.restaurant_item_id)
        # An id that exists but belongs to another restaurant and one that
        # exists nowhere are the same answer: it is not on this menu. Saying
        # which would turn the endpoint into an item-id probe.
        if item is None or item.restaurant_id != restaurant.id:
            raise _rejected(
                f"Item {line.restaurant_item_id} is not on this restaurant's menu"
            )
        if not item.is_available:
            raise _rejected(f"Item {line.restaurant_item_id} is not available")
        if line.quantity <= 0:
            raise _rejected("Quantity must be greater than zero")

        order.items.append(
            OrderItem(
                restaurant_item_id=item.id,
                quantity=line.quantity,
                unit_price=item.price,
            )
        )
        total += item.price * line.quantity

    order.total_amount = total
    db.add(order)
    db.commit()
    db.refresh(order)
    return order
