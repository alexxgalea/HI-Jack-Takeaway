from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models.enums import OrderStatus
from app.models.order import Order

# The delivery pipeline, written down once. Every status names exactly the
# set it may move to next, and `delivered` names the empty set because it is
# the end of the line - there is no cancellation in this scope.
ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.pending: {OrderStatus.accepted},
    OrderStatus.accepted: {OrderStatus.out_for_delivery},
    OrderStatus.out_for_delivery: {OrderStatus.delivered},
    OrderStatus.delivered: set(),
}


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    """Whether `target` is the legal next step from `current`.

    A status missing from the table has no exits rather than raising: if the
    enum ever grows a member and this map is not updated, the endpoint refuses
    the move with a 409 instead of collapsing into a 500.
    """
    return target in ALLOWED_TRANSITIONS.get(current, set())


def assert_transition(current: OrderStatus, target: OrderStatus) -> None:
    """Raise unless the move is legal.

    409 rather than 400: the request is well-formed and the caller is allowed
    to make it - it just conflicts with the state the order is in right now.
    Both ends are named in the detail because an admin driving an order needs
    to know what it had already moved on to.
    """
    if not can_transition(current, target):
        raise ConflictError(
            f"Cannot move an order from {current.value} to {target.value}"
        )


def set_order_status(db: Session, order: Order, target: OrderStatus) -> Order:
    """Advance one order, holding its row for the duration.

    The refresh takes `SELECT ... FOR UPDATE` on the row, so the status the
    check reads is the committed one and no concurrent admin can move the same
    order between the check and the write. Without the lock two requests could
    both read `accepted` and both be allowed through, and the order would take
    a step it never legally made.

    `updated_at` is bumped by the column's own `onupdate=func.now()` when this
    UPDATE flushes - it is not restated here so there is a single definition of
    when that clock moves.
    """
    db.refresh(order, with_for_update=True)
    assert_transition(order.status, target)

    order.status = target
    db.commit()
    db.refresh(order)
    return order
