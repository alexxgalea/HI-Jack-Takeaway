from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, TypeVar

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import AdminUser, DbSession
from app.models.enums import OrderStatus
from app.models.order import Order
from app.models.restaurant import Restaurant
from app.models.user import User
from app.schemas.order import OrderOut
from app.schemas.pagination import PaginatedResponse
from app.schemas.user import UserAdminUpdate, UserOut

# Every route below takes `admin: AdminUser`, one by one, rather than stating
# the requirement once in the router's own `dependencies=[...]`: M2 fixed the
# `AdminUser` alias as the only form a router may use, and the router-level
# spelling needs the inline dependency call that convention rules out - the
# grep in tests/test_auth.py holds the whole package to it, comments included.
router = APIRouter(prefix="/admin", tags=["admin"])

# Paging bounds are the API's, not this router's - the same ones `GET /orders`
# already enforces. A caller who learns them on one listing knows them on all.
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]

T = TypeVar("T")


def _as_utc(moment: datetime | None) -> datetime | None:
    """Read a naive filter bound as UTC.

    `created_at` is `timestamptz` and every stored value is UTC, but a client
    may well send `2026-01-01T00:00:00` with no offset. Left alone, psycopg
    hands that over as a naive timestamp and Postgres resolves it against
    whatever the session's TimeZone happens to be - so the same query would
    select different orders on two differently configured servers. Pinning it
    to UTC here makes the bound mean one thing everywhere.
    """
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


def _order_filters(
    order_status: OrderStatus | None,
    restaurant_id: int | None,
    customer_id: int | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> list[ColumnElement[bool]]:
    """Turn the query string into conditions, skipping the ones not asked for.

    Each filter is independent and they are ANDed, so they compose: `status`
    alone narrows by status, `status` with a date range narrows by both. An
    omitted filter adds no condition at all rather than a match-everything one.

    `restaurant_id` and `customer_id` are not checked for existence. They are
    filters, not lookups - an id that matches nothing yields an empty page,
    which is also the honest answer for a real id with no orders yet.
    """
    filters: list[ColumnElement[bool]] = []
    if order_status is not None:
        filters.append(Order.status == order_status)
    if restaurant_id is not None:
        filters.append(Order.restaurant_id == restaurant_id)
    if customer_id is not None:
        filters.append(Order.customer_id == customer_id)
    # Both bounds inclusive: a range given as two dates should contain the
    # orders placed on them.
    if created_from is not None:
        filters.append(Order.created_at >= created_from)
    if created_to is not None:
        filters.append(Order.created_at <= created_to)
    return filters


def _assert_range_is_ordered(
    created_from: datetime | None, created_to: datetime | None
) -> None:
    """Refuse a range that runs backwards.

    `created_from` after `created_to` can never match a row, so answering with
    an empty page would look like "no orders" when it is really "no such
    range". 422 puts it where the other malformed-input answers are.
    """
    if (
        created_from is not None
        and created_to is not None
        and created_from > created_to
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="created_from must not be later than created_to",
        )


def _page(
    db: Session, stmt: Select[tuple[T]], limit: int, offset: int
) -> tuple[Sequence[T], int]:
    """Run one statement twice: once counted whole, once sliced.

    The count is derived from the same statement the page comes from, so the
    two can never drift apart - a filter added to the listing is counted by
    construction instead of having to be repeated in a second hand-written
    query. `order_by(None)` drops the sort first: ordering rows only to count
    them is work Postgres does not need to do.
    """
    total = db.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )
    rows = db.scalars(stmt.limit(limit).offset(offset)).all()
    return rows, total or 0


def _orders_newest_first(
    filters: list[ColumnElement[bool]],
) -> Select[tuple[Order]]:
    # `created_at` is the transaction clock, so orders placed together share
    # it; the id tiebreaker is what keeps paging stable across requests.
    # `selectinload` because `OrderOut` carries the lines - without it each row
    # on the page costs another query.
    return (
        select(Order)
        .where(*filters)
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc(), Order.id.desc())
    )


# --- orders ---------------------------------------------------------------


@router.get("/orders")
def list_orders(
    db: DbSession,
    admin: AdminUser,
    # `status` on the wire; `order_status` in Python, because `status` is the
    # imported module this file raises its HTTP codes from.
    order_status: Annotated[OrderStatus | None, Query(alias="status")] = None,
    restaurant_id: int | None = None,
    customer_id: int | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> PaginatedResponse[OrderOut]:
    """Every order in the system, newest first, narrowed by any combination
    of the filters.

    No response_model= on the decorator here or below: the return annotation
    already names the response schema, so restating it would give the route two
    places to disagree with itself.
    """
    # Normalised before they are compared, not after: one bound sent with an
    # offset and the other without are both legal, and comparing a naive
    # datetime to an aware one raises TypeError - a 500 for two values that
    # make perfect sense once they are on the same clock.
    since, until = _as_utc(created_from), _as_utc(created_to)
    _assert_range_is_ordered(since, until)

    stmt = _orders_newest_first(
        _order_filters(order_status, restaurant_id, customer_id, since, until)
    )
    orders, total = _page(db, stmt, limit, offset)
    return PaginatedResponse[OrderOut](
        items=orders, total=total, limit=limit, offset=offset
    )


@router.get("/restaurants/{restaurant_id}/orders")
def list_restaurant_orders(
    restaurant_id: int,
    db: DbSession,
    admin: AdminUser,
    limit: Limit = 20,
    offset: Offset = 0,
) -> PaginatedResponse[OrderOut]:
    """One restaurant's order book.

    Reachable as `/admin/orders?restaurant_id=` too, with one difference that
    is the point of the route: here the restaurant is looked up first, so an id
    that does not exist is a 404 rather than an empty page. Asking a named
    restaurant for its orders and being handed silence is a bug report waiting
    to happen.
    """
    if db.get(Restaurant, restaurant_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Restaurant not found"
        )

    stmt = _orders_newest_first([Order.restaurant_id == restaurant_id])
    orders, total = _page(db, stmt, limit, offset)
    return PaginatedResponse[OrderOut](
        items=orders, total=total, limit=limit, offset=offset
    )


# --- users ----------------------------------------------------------------


@router.get("/users")
def list_users(
    db: DbSession, admin: AdminUser, limit: Limit = 20, offset: Offset = 0
) -> PaginatedResponse[UserOut]:
    """The account list, oldest first.

    By id rather than newest-first: `UserOut` exposes no timestamp, so a
    descending list would be ordered by something the caller cannot see.
    `UserOut` is also what keeps `hashed_password` out of the page.
    """
    stmt = select(User).order_by(User.id)
    users, total = _page(db, stmt, limit, offset)
    return PaginatedResponse[UserOut](
        items=users, total=total, limit=limit, offset=offset
    )


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int, payload: UserAdminUpdate, db: DbSession, admin: AdminUser
) -> User:
    """Change an account's role or activation. Nothing else is editable here.

    Deactivating is the closest thing to deletion in this scope, and it is
    enough: `get_current_user` rejects an inactive user's token even while it is
    still in date, so revoking access does not need a token blacklist.

    Nothing stops an admin from demoting or deactivating themselves - including
    the last admin, which locks the role out of the running system. The plan
    names no such guard, so none is invented here.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user
