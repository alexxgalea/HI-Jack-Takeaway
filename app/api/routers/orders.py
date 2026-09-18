from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.models.enums import UserRole
from app.models.order import Order
from app.schemas.order import OrderCreate, OrderOut, OrderStatusUpdate
from app.services.order_service import create_order
from app.services.transitions import set_order_status

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def place_order(payload: OrderCreate, db: DbSession, user: CurrentUser) -> Order:
    # Thin on purpose: every ordering rule lives in the service.
    return create_order(db, user, payload)


@router.get("", response_model=list[OrderOut])
def list_my_orders(
    db: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Sequence[Order]:
    # Own orders only - the customer filter is not a caller-supplied one.
    stmt = (
        select(Order)
        .where(Order.customer_id == user.id)
        .options(selectinload(Order.items))
        # `created_at` is the transaction clock, so orders placed together can
        # share it; the id keeps the page order stable.
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return db.scalars(stmt).all()


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: DbSession, user: CurrentUser) -> Order:
    order = db.get(Order, order_id)
    # Someone else's order reads as absent rather than forbidden: a 403 here
    # would confirm the id exists to anyone willing to count upwards.
    if order is None or (order.customer_id != user.id and user.role != UserRole.admin):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )
    return order


@router.patch("/{order_id}/status", response_model=OrderOut)
def update_order_status(
    order_id: int, payload: OrderStatusUpdate, db: DbSession, admin: AdminUser
) -> Order:
    """Move an order one step along the pipeline. Staff only.

    A missing order is a plain 404 here, not the owner-shaped one above: the
    caller is already an admin, so there is nothing left to hide from them.
    """
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )
    return set_order_status(db, order, payload.status)
