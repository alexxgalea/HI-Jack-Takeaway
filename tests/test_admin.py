from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routers import admin
from app.models.enums import OrderStatus, UserRole
from app.models.order import Order
from app.models.order_item import OrderItem
from app.models.restaurant import Restaurant
from app.models.restaurant_item import RestaurantItem
from app.models.user import User
from app.schemas.order import OrderOut
from app.schemas.pagination import PaginatedResponse
from app.schemas.user import UserOut
from tests.conftest import USER_PASSWORD
from tests.test_auth import auth_header

# Five fixed days, so a date-range filter has something to cut between. The
# rows below carry these explicitly rather than taking the column's default:
# Postgres `now()` is the transaction clock and the `db_session` fixture runs
# each test inside one transaction, so every default-stamped row in a test
# shares a single `created_at` and no range could separate them.
DAYS: tuple[datetime, ...] = tuple(datetime(2026, 1, day, 12, 0, tzinfo=UTC) for day in range(1, 6))


# --- fixtures -------------------------------------------------------------


@pytest.fixture
def restaurant(db_session: Session) -> Restaurant:
    return _add_restaurant(db_session, "Pizza Place", "1 Napoli Street")


@pytest.fixture
def other_restaurant(db_session: Session) -> Restaurant:
    return _add_restaurant(db_session, "Burger Joint", "42 Grill Avenue")


def _add_restaurant(db: Session, name: str, address: str) -> Restaurant:
    restaurant = Restaurant(name=name, address=address)
    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)
    return restaurant


def _add_item(db: Session, restaurant: Restaurant, name: str, price: str) -> RestaurantItem:
    item = RestaurantItem(
        restaurant_id=restaurant.id,
        name=name,
        description=None,
        price=Decimal(price),
        is_available=True,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@pytest.fixture
def margherita(db_session: Session, restaurant: Restaurant) -> RestaurantItem:
    return _add_item(db_session, restaurant, "Margherita", "12.99")


@pytest.fixture
def cheeseburger(db_session: Session, other_restaurant: Restaurant) -> RestaurantItem:
    return _add_item(db_session, other_restaurant, "Cheeseburger", "9.99")


@pytest.fixture
def other_user(db_session: Session) -> User:
    user = User(
        email="other@example.com",
        hashed_password="not-a-real-hash",
        full_name="Other Customer",
        role=UserRole.user,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _add_order(
    db: Session,
    customer: User,
    restaurant: Restaurant,
    item: RestaurantItem,
    order_status: OrderStatus,
    created_at: datetime,
    quantity: int = 1,
) -> Order:
    """One finished order, written straight to the table.

    Not placed through `POST /orders` and not walked through `PATCH
    /orders/{id}/status`: those paths are M4's and M5's to prove, and neither
    lets a test choose `created_at` or drop an order into `delivered` in one
    step. These rows exist to be *read*, and the reading is what M6 is about.
    """
    order = Order(
        customer_id=customer.id,
        restaurant_id=restaurant.id,
        status=order_status,
        delivery_address="10 Customer Lane",
        total_amount=item.price * quantity,
        created_at=created_at,
        updated_at=created_at,
        items=[OrderItem(restaurant_item_id=item.id, quantity=quantity, unit_price=item.price)],
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@dataclass(frozen=True)
class Spread:
    """Five orders chosen so every filter has both a match and a non-match.

    | field                | customer   | restaurant       | status           | day |
    | -------------------- | ---------- | ---------------- | ---------------- | --- |
    | pending_pizza        | user       | restaurant       | pending          | 1   |
    | accepted_pizza       | user       | restaurant       | accepted         | 2   |
    | delivered_burger     | user       | other_restaurant | delivered        | 3   |
    | others_pending_pizza | other_user | restaurant       | pending          | 4   |
    | others_out_burger    | other_user | other_restaurant | out_for_delivery | 5   |
    """

    pending_pizza: Order
    accepted_pizza: Order
    delivered_burger: Order
    others_pending_pizza: Order
    others_out_burger: Order

    @property
    def newest_first(self) -> list[Order]:
        return [
            self.others_out_burger,
            self.others_pending_pizza,
            self.delivered_burger,
            self.accepted_pizza,
            self.pending_pizza,
        ]

    @property
    def ids(self) -> set[int]:
        return {order.id for order in self.newest_first}


@pytest.fixture
def spread(
    db_session: Session,
    user: User,
    other_user: User,
    restaurant: Restaurant,
    other_restaurant: Restaurant,
    margherita: RestaurantItem,
    cheeseburger: RestaurantItem,
) -> Spread:
    return Spread(
        pending_pizza=_add_order(
            db_session, user, restaurant, margherita, OrderStatus.pending, DAYS[0]
        ),
        accepted_pizza=_add_order(
            db_session, user, restaurant, margherita, OrderStatus.accepted, DAYS[1]
        ),
        delivered_burger=_add_order(
            db_session,
            user,
            other_restaurant,
            cheeseburger,
            OrderStatus.delivered,
            DAYS[2],
        ),
        others_pending_pizza=_add_order(
            db_session,
            other_user,
            restaurant,
            margherita,
            OrderStatus.pending,
            DAYS[3],
        ),
        others_out_burger=_add_order(
            db_session,
            other_user,
            other_restaurant,
            cheeseburger,
            OrderStatus.out_for_delivery,
            DAYS[4],
        ),
    )


# --- helpers --------------------------------------------------------------


async def _get(client: AsyncClient, token: str, path: str, **params: object):
    return await client.get(path, headers=auth_header(token), params=params)


def _ids(response) -> list[int]:
    return [row["id"] for row in response.json()["items"]]


def _day(index: int) -> str:
    return DAYS[index].isoformat()


# --- every /admin route is an admin route ---------------------------------


def _admin_routes() -> list[tuple[str, str]]:
    """Every method/path pair the admin router publishes, read off the router.

    Discovered rather than listed, so a route added to `admin.py` without a
    thought for authorization is covered by the two tests below the moment it
    exists - a hand-written list would simply not mention it.
    """
    routes = []
    for route in admin.router.routes:
        path = route.path.replace("{restaurant_id}", "1").replace("{user_id}", "1")
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            routes.append((method, path))
    return sorted(routes)


def test_the_discovered_route_list_is_the_whole_router() -> None:
    # If this drifts, the two tests below are quietly checking less than they
    # look like they are.
    assert _admin_routes() == [
        ("GET", "/admin/orders"),
        ("GET", "/admin/restaurants/1/orders"),
        ("GET", "/admin/users"),
        ("PATCH", "/admin/users/1"),
    ]


@pytest.mark.parametrize(("method", "path"), _admin_routes())
async def test_every_admin_route_rejects_a_non_admin_with_403(
    client: AsyncClient, user_token: str, method: str, path: str
) -> None:
    response = await client.request(method, path, headers=auth_header(user_token), json={})
    assert response.status_code == 403


@pytest.mark.parametrize(("method", "path"), _admin_routes())
async def test_every_admin_route_rejects_an_anonymous_caller_with_401(
    client: AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path, json={})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(("method", "path"), _admin_routes())
async def test_every_admin_route_rejects_a_deactivated_admin_with_401(
    client: AsyncClient,
    db_session: Session,
    admin_token: str,
    admin: User,
    method: str,
    path: str,
) -> None:
    # A still-in-date token whose account has since been switched off. This is
    # the revocation story for `PATCH /admin/users/{id}`, so it is worth
    # proving on the admin surface itself.
    admin.is_active = False
    db_session.commit()

    response = await client.request(method, path, headers=auth_header(admin_token), json={})
    assert response.status_code == 401


# --- GET /admin/orders: the whole book ------------------------------------


async def test_admin_sees_every_order_whoever_placed_it(
    client: AsyncClient, admin_token: str, spread: Spread
) -> None:
    response = await _get(client, admin_token, "/admin/orders", limit=100)
    assert response.status_code == 200
    assert spread.ids <= set(_ids(response))


async def test_orders_come_back_newest_first(
    client: AsyncClient, admin_token: str, spread: Spread
) -> None:
    response = await _get(client, admin_token, "/admin/orders", limit=100)

    listed = _ids(response)
    mine = [order_id for order_id in listed if order_id in spread.ids]
    assert mine == [order.id for order in spread.newest_first]


async def test_the_page_carries_the_order_lines_and_the_full_order_shape(
    client: AsyncClient, admin_token: str, spread: Spread, margherita: RestaurantItem
) -> None:
    response = await _get(
        client,
        admin_token,
        "/admin/orders",
        customer_id=spread.pending_pizza.customer_id,
    )

    [row] = [r for r in response.json()["items"] if r["id"] == spread.pending_pizza.id]
    assert set(row) == set(OrderOut.model_fields)
    assert [line["restaurant_item_id"] for line in row["items"]] == [margherita.id]
    assert Decimal(row["total_amount"]) == Decimal("12.99")


async def test_the_envelope_has_exactly_the_paginated_fields(
    client: AsyncClient, admin_token: str, spread: Spread
) -> None:
    response = await _get(client, admin_token, "/admin/orders")
    assert set(response.json()) == set(PaginatedResponse.model_fields)


# --- GET /admin/orders: one filter at a time ------------------------------


@pytest.mark.parametrize(
    ("order_status", "expected"),
    [
        (OrderStatus.pending, ("pending_pizza", "others_pending_pizza")),
        (OrderStatus.accepted, ("accepted_pizza",)),
        (OrderStatus.out_for_delivery, ("others_out_burger",)),
        (OrderStatus.delivered, ("delivered_burger",)),
    ],
)
async def test_filtering_by_status(
    client: AsyncClient,
    admin_token: str,
    spread: Spread,
    order_status: OrderStatus,
    expected: tuple[str, ...],
) -> None:
    response = await _get(
        client, admin_token, "/admin/orders", status=order_status.value, limit=100
    )

    wanted = {getattr(spread, name).id for name in expected}
    assert set(_ids(response)) & spread.ids == wanted
    assert {row["status"] for row in response.json()["items"]} == {order_status.value}


async def test_filtering_by_restaurant(
    client: AsyncClient, admin_token: str, spread: Spread, restaurant: Restaurant
) -> None:
    response = await _get(client, admin_token, "/admin/orders", restaurant_id=restaurant.id)
    assert set(_ids(response)) == {
        spread.others_pending_pizza.id,
        spread.accepted_pizza.id,
        spread.pending_pizza.id,
    }


async def test_filtering_by_customer(
    client: AsyncClient, admin_token: str, spread: Spread, other_user: User
) -> None:
    response = await _get(client, admin_token, "/admin/orders", customer_id=other_user.id)
    assert set(_ids(response)) == {
        spread.others_pending_pizza.id,
        spread.others_out_burger.id,
    }


async def test_filtering_from_a_date_is_inclusive_of_that_day(
    client: AsyncClient, admin_token: str, spread: Spread
) -> None:
    response = await _get(client, admin_token, "/admin/orders", created_from=_day(3), limit=100)
    assert set(_ids(response)) & spread.ids == {
        spread.others_pending_pizza.id,
        spread.others_out_burger.id,
    }


async def test_filtering_to_a_date_is_inclusive_of_that_day(
    client: AsyncClient, admin_token: str, spread: Spread
) -> None:
    response = await _get(client, admin_token, "/admin/orders", created_to=_day(1), limit=100)
    assert set(_ids(response)) & spread.ids == {
        spread.pending_pizza.id,
        spread.accepted_pizza.id,
    }


async def test_filtering_between_two_dates(
    client: AsyncClient, admin_token: str, spread: Spread
) -> None:
    response = await _get(
        client,
        admin_token,
        "/admin/orders",
        created_from=_day(1),
        created_to=_day(3),
        limit=100,
    )
    assert set(_ids(response)) & spread.ids == {
        spread.accepted_pizza.id,
        spread.delivered_burger.id,
        spread.others_pending_pizza.id,
    }


async def test_a_naive_bound_is_read_as_utc(
    client: AsyncClient, admin_token: str, spread: Spread
) -> None:
    # The same instant with and without its offset must select the same orders,
    # whatever the database session's TimeZone is set to.
    aware = await _get(client, admin_token, "/admin/orders", created_from=_day(4), limit=100)
    naive = await _get(
        client,
        admin_token,
        "/admin/orders",
        created_from=DAYS[4].replace(tzinfo=None).isoformat(),
        limit=100,
    )
    assert _ids(naive) == _ids(aware) == [spread.others_out_burger.id]


async def test_an_offset_bound_is_honoured_not_dropped(
    client: AsyncClient, admin_token: str, spread: Spread
) -> None:
    # 13:00+01:00 is 12:00Z - the same instant day 5 was created at, so an
    # inclusive `created_from` keeps it. Were the offset dropped, the bound
    # would read as 13:00Z, fall after day 5, and cut it out.
    bound = (DAYS[4] + timedelta(hours=1)).isoformat().replace("+00:00", "+01:00")

    response = await _get(client, admin_token, "/admin/orders", created_from=bound, limit=100)
    assert spread.others_out_burger.id in _ids(response)


# --- GET /admin/orders: filters compose ----------------------------------


async def test_status_and_restaurant_compose(
    client: AsyncClient, admin_token: str, spread: Spread, restaurant: Restaurant
) -> None:
    response = await _get(
        client,
        admin_token,
        "/admin/orders",
        status=OrderStatus.pending.value,
        restaurant_id=restaurant.id,
        limit=100,
    )
    # Two orders are pending and three are this restaurant's; only these two
    # are both.
    assert set(_ids(response)) == {
        spread.pending_pizza.id,
        spread.others_pending_pizza.id,
    }


async def test_customer_and_date_range_compose(
    client: AsyncClient, admin_token: str, spread: Spread, user: User
) -> None:
    response = await _get(
        client,
        admin_token,
        "/admin/orders",
        customer_id=user.id,
        created_from=_day(1),
        created_to=_day(2),
        limit=100,
    )
    assert set(_ids(response)) == {spread.accepted_pizza.id, spread.delivered_burger.id}


async def test_all_four_filters_compose_down_to_one_order(
    client: AsyncClient,
    admin_token: str,
    spread: Spread,
    user: User,
    restaurant: Restaurant,
) -> None:
    response = await _get(
        client,
        admin_token,
        "/admin/orders",
        status=OrderStatus.accepted.value,
        restaurant_id=restaurant.id,
        customer_id=user.id,
        created_from=_day(0),
        created_to=_day(4),
    )
    assert _ids(response) == [spread.accepted_pizza.id]
    assert response.json()["total"] == 1


async def test_filters_that_contradict_each_other_return_an_empty_page(
    client: AsyncClient,
    admin_token: str,
    spread: Spread,
    other_user: User,
    restaurant: Restaurant,
) -> None:
    # `other_user` did order from `restaurant`, but not while delivered.
    response = await _get(
        client,
        admin_token,
        "/admin/orders",
        status=OrderStatus.delivered.value,
        restaurant_id=restaurant.id,
        customer_id=other_user.id,
    )
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


async def test_an_id_that_matches_nothing_is_an_empty_page_not_a_404(
    client: AsyncClient, admin_token: str, spread: Spread
) -> None:
    # A filter is not a lookup: `/admin/orders?restaurant_id=` says "narrow to
    # this", and nothing matching is a legitimate answer. The 404 lives on
    # `/admin/restaurants/{id}/orders`, which does look the restaurant up.
    response = await _get(client, admin_token, "/admin/orders", restaurant_id=10_000_000)
    assert response.status_code == 200
    assert response.json()["total"] == 0


# --- GET /admin/orders: invalid filter values ----------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"status": "cancelled"},
        {"status": ""},
        {"restaurant_id": "not-an-id"},
        {"customer_id": "1.5"},
        {"created_from": "yesterday"},
        {"created_to": "2026-13-01"},
        {"limit": 0},
        {"limit": 101},
        {"limit": "many"},
        {"offset": -1},
    ],
    ids=lambda params: "-".join(f"{k}={v}" for k, v in params.items()),
)
async def test_an_invalid_filter_value_is_422(
    client: AsyncClient, admin_token: str, params: dict[str, object]
) -> None:
    response = await _get(client, admin_token, "/admin/orders", **params)
    assert response.status_code == 422


async def test_a_range_that_runs_backwards_is_422(client: AsyncClient, admin_token: str) -> None:
    response = await _get(
        client, admin_token, "/admin/orders", created_from=_day(4), created_to=_day(0)
    )
    assert response.status_code == 422
    assert response.json()["detail"]


@pytest.mark.parametrize(
    ("created_from", "created_to"),
    [
        (DAYS[4].isoformat(), DAYS[0].replace(tzinfo=None).isoformat()),
        (DAYS[4].replace(tzinfo=None).isoformat(), DAYS[0].isoformat()),
    ],
    ids=["aware-from-naive-to", "naive-from-aware-to"],
)
async def test_a_backwards_range_is_422_even_with_one_bound_offsetless(
    client: AsyncClient, admin_token: str, created_from: str, created_to: str
) -> None:
    # The two bounds are put on the same clock before they are compared:
    # comparing a naive datetime with an aware one raises, and the caller who
    # wrote one of them without an offset deserves the same 422 as everyone else.
    response = await _get(
        client,
        admin_token,
        "/admin/orders",
        created_from=created_from,
        created_to=created_to,
    )
    assert response.status_code == 422


async def test_a_mixed_awareness_range_that_is_ordered_still_filters(
    client: AsyncClient, admin_token: str, spread: Spread
) -> None:
    response = await _get(
        client,
        admin_token,
        "/admin/orders",
        created_from=DAYS[1].replace(tzinfo=None).isoformat(),
        created_to=DAYS[2].isoformat(),
        limit=100,
    )
    assert response.status_code == 200
    assert set(_ids(response)) & spread.ids == {
        spread.accepted_pizza.id,
        spread.delivered_burger.id,
    }


async def test_a_range_whose_ends_are_equal_is_allowed(
    client: AsyncClient, admin_token: str, spread: Spread
) -> None:
    # Both bounds are inclusive, so from == to is the single instant, not an
    # empty or invalid range.
    response = await _get(
        client, admin_token, "/admin/orders", created_from=_day(2), created_to=_day(2)
    )
    assert response.status_code == 200
    assert _ids(response) == [spread.delivered_burger.id]


async def test_authorization_is_checked_before_the_filters(
    client: AsyncClient, user_token: str
) -> None:
    # A non-admin sending nonsense gets 403, not 422: what they may do is
    # settled before what they asked for is parsed.
    response = await _get(client, user_token, "/admin/orders", status="cancelled")
    assert response.status_code == 403


# --- pagination metadata -------------------------------------------------


async def test_total_counts_every_match_not_the_rows_returned(
    client: AsyncClient, admin_token: str, spread: Spread, user: User
) -> None:
    response = await _get(client, admin_token, "/admin/orders", customer_id=user.id, limit=1)

    body = response.json()
    assert len(body["items"]) == 1
    assert body["total"] == 3
    assert body["limit"] == 1
    assert body["offset"] == 0


async def test_total_is_the_row_count_the_database_reports(
    client: AsyncClient, db_session: Session, admin_token: str, spread: Spread
) -> None:
    response = await _get(client, admin_token, "/admin/orders", limit=1)
    assert response.json()["total"] == db_session.scalar(select(func.count()).select_from(Order))


async def test_limit_and_offset_are_echoed_back_as_sent(
    client: AsyncClient, admin_token: str, spread: Spread
) -> None:
    response = await _get(client, admin_token, "/admin/orders", limit=2, offset=3)
    body = response.json()
    assert (body["limit"], body["offset"]) == (2, 3)


async def test_paging_walks_every_match_exactly_once(
    client: AsyncClient, admin_token: str, spread: Spread, user: User
) -> None:
    pages = [
        _ids(
            await _get(
                client,
                admin_token,
                "/admin/orders",
                customer_id=user.id,
                limit=2,
                offset=offset,
            )
        )
        for offset in (0, 2, 4)
    ]
    assert pages == [
        [spread.delivered_burger.id, spread.accepted_pizza.id],
        [spread.pending_pizza.id],
        [],
    ]


async def test_an_offset_past_the_end_is_an_empty_page_with_the_real_total(
    client: AsyncClient, admin_token: str, spread: Spread, user: User
) -> None:
    response = await _get(client, admin_token, "/admin/orders", customer_id=user.id, offset=99)
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 3


async def test_the_default_page_is_twenty_from_the_start(
    client: AsyncClient, admin_token: str, spread: Spread
) -> None:
    body = (await _get(client, admin_token, "/admin/orders")).json()
    assert (body["limit"], body["offset"]) == (20, 0)


# --- GET /admin/users ----------------------------------------------------


async def test_listing_users_carries_every_account_and_no_password_field(
    client: AsyncClient, admin_token: str, user: User, other_user: User
) -> None:
    response = await _get(client, admin_token, "/admin/users", limit=100)
    assert response.status_code == 200

    rows = {row["id"]: row for row in response.json()["items"]}
    assert {user.id, other_user.id} <= set(rows)
    assert set(rows[user.id]) == set(UserOut.model_fields)
    # Belt and braces on the one field that must never travel.
    assert "hashed_password" not in response.text
    assert "password" not in response.text


async def test_users_are_listed_oldest_first(
    client: AsyncClient, admin_token: str, user: User, admin: User, other_user: User
) -> None:
    listed = _ids(await _get(client, admin_token, "/admin/users", limit=100))
    assert listed == sorted(listed)


async def test_the_user_total_is_the_row_count_the_database_reports(
    client: AsyncClient,
    db_session: Session,
    admin_token: str,
    user: User,
    other_user: User,
) -> None:
    response = await _get(client, admin_token, "/admin/users", limit=1)

    body = response.json()
    assert len(body["items"]) == 1
    assert body["total"] == db_session.scalar(select(func.count()).select_from(User))


async def test_user_paging_walks_every_account_exactly_once(
    client: AsyncClient,
    db_session: Session,
    admin_token: str,
    user: User,
    admin: User,
    other_user: User,
) -> None:
    total = db_session.scalar(select(func.count()).select_from(User))

    walked: list[int] = []
    for offset in range(0, total + 1, 2):
        walked += _ids(await _get(client, admin_token, "/admin/users", limit=2, offset=offset))
    assert len(walked) == total == len(set(walked))
    assert {user.id, admin.id, other_user.id} <= set(walked)


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
async def test_invalid_user_paging_values_are_422(
    client: AsyncClient, admin_token: str, params: dict[str, object]
) -> None:
    response = await _get(client, admin_token, "/admin/users", **params)
    assert response.status_code == 422


# --- PATCH /admin/users/{id} --------------------------------------------


async def test_promoting_a_user_makes_their_existing_token_an_admin_one(
    client: AsyncClient, admin_token: str, user_token: str, user: User
) -> None:
    response = await client.patch(
        f"/admin/users/{user.id}",
        headers=auth_header(admin_token),
        json={"role": UserRole.admin.value},
    )
    assert response.status_code == 200
    assert response.json()["role"] == UserRole.admin.value

    # The role is read from the row on every request, not trusted from the
    # token's `role` claim, so the token minted before the promotion now opens
    # an admin route.
    promoted = await _get(client, user_token, "/admin/users")
    assert promoted.status_code == 200


async def test_demoting_an_admin_closes_the_admin_routes_to_them(
    client: AsyncClient, admin_token: str, admin: User, user_token: str, user: User
) -> None:
    await client.patch(
        f"/admin/users/{user.id}",
        headers=auth_header(admin_token),
        json={"role": UserRole.admin.value},
    )
    demoted = await client.patch(
        f"/admin/users/{user.id}",
        headers=auth_header(admin_token),
        json={"role": UserRole.user.value},
    )
    assert demoted.json()["role"] == UserRole.user.value

    assert (await _get(client, user_token, "/admin/users")).status_code == 403


async def test_deactivating_a_user_revokes_their_still_valid_token(
    client: AsyncClient, admin_token: str, user_token: str, user: User
) -> None:
    response = await client.patch(
        f"/admin/users/{user.id}",
        headers=auth_header(admin_token),
        json={"is_active": False},
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    # No blacklist involved: `get_current_user` re-reads `is_active`.
    me = await client.get("/auth/me", headers=auth_header(user_token))
    assert me.status_code == 401


async def test_deactivating_a_user_also_closes_the_login_door(
    client: AsyncClient, admin_token: str, user: User
) -> None:
    await client.patch(
        f"/admin/users/{user.id}",
        headers=auth_header(admin_token),
        json={"is_active": False},
    )
    response = await client.post(
        "/auth/login", data={"username": user.email, "password": USER_PASSWORD}
    )
    assert response.status_code == 401


async def test_reactivating_a_user_lets_them_back_in(
    client: AsyncClient, admin_token: str, user_token: str, user: User
) -> None:
    for is_active in (False, True):
        response = await client.patch(
            f"/admin/users/{user.id}",
            headers=auth_header(admin_token),
            json={"is_active": is_active},
        )
        assert response.json()["is_active"] is is_active

    me = await client.get("/auth/me", headers=auth_header(user_token))
    assert me.status_code == 200


async def test_both_fields_move_in_one_request(
    client: AsyncClient, db_session: Session, admin_token: str, user: User
) -> None:
    response = await client.patch(
        f"/admin/users/{user.id}",
        headers=auth_header(admin_token),
        json={"role": UserRole.admin.value, "is_active": False},
    )
    assert response.status_code == 200

    db_session.expire_all()
    stored = db_session.get(User, user.id)
    assert (stored.role, stored.is_active) == (UserRole.admin, False)


async def test_an_omitted_field_is_left_alone(
    client: AsyncClient, db_session: Session, admin_token: str, user: User
) -> None:
    response = await client.patch(
        f"/admin/users/{user.id}",
        headers=auth_header(admin_token),
        json={"is_active": False},
    )
    assert response.json()["role"] == UserRole.user.value

    db_session.expire_all()
    assert db_session.get(User, user.id).role == UserRole.user


async def test_an_empty_body_changes_nothing(
    client: AsyncClient, db_session: Session, admin_token: str, user: User
) -> None:
    before = UserOut.model_validate(user).model_dump()

    response = await client.patch(
        f"/admin/users/{user.id}", headers=auth_header(admin_token), json={}
    )
    assert response.status_code == 200

    db_session.expire_all()
    assert UserOut.model_validate(db_session.get(User, user.id)).model_dump() == before


async def test_fields_outside_the_admin_two_are_ignored(
    client: AsyncClient, db_session: Session, admin_token: str, user: User
) -> None:
    # This route is not an account editor: an address or a password smuggled
    # into the body must not land, or promoting an account would double as a
    # way to take it over.
    response = await client.patch(
        f"/admin/users/{user.id}",
        headers=auth_header(admin_token),
        json={
            "email": "attacker@example.com",
            "full_name": "Renamed",
            "hashed_password": "overwritten",
            "is_active": False,
        },
    )
    assert response.status_code == 200
    assert response.json()["email"] == user.email

    db_session.expire_all()
    stored = db_session.get(User, user.id)
    assert stored.email == "user@example.com"
    assert stored.full_name == "User Fixture"
    assert stored.hashed_password != "overwritten"


@pytest.mark.parametrize(
    "body",
    [
        {"role": "superuser"},
        {"role": ""},
        {"role": None},
        {"is_active": "maybe"},
    ],
    ids=["unknown-role", "empty-role", "null-role", "non-boolean-is-active"],
)
async def test_an_invalid_patch_body_is_422(
    client: AsyncClient, admin_token: str, user: User, body: dict[str, object]
) -> None:
    response = await client.patch(
        f"/admin/users/{user.id}", headers=auth_header(admin_token), json=body
    )
    assert response.status_code == 422


async def test_patching_an_unknown_user_is_404(client: AsyncClient, admin_token: str) -> None:
    response = await client.patch(
        "/admin/users/10000000",
        headers=auth_header(admin_token),
        json={"is_active": False},
    )
    assert response.status_code == 404


async def test_an_admin_can_lock_themselves_out(
    client: AsyncClient, admin_token: str, admin: User
) -> None:
    # Documented behaviour, not an oversight: the plan names no last-admin
    # guard, so deactivating yourself is allowed and takes effect at once.
    response = await client.patch(
        f"/admin/users/{admin.id}",
        headers=auth_header(admin_token),
        json={"is_active": False},
    )
    assert response.status_code == 200
    assert (await _get(client, admin_token, "/admin/users")).status_code == 401


# --- GET /admin/restaurants/{id}/orders ----------------------------------


async def test_a_restaurants_order_book_holds_only_its_own_orders(
    client: AsyncClient, admin_token: str, spread: Spread, restaurant: Restaurant
) -> None:
    response = await _get(
        client, admin_token, f"/admin/restaurants/{restaurant.id}/orders", limit=100
    )
    assert response.status_code == 200
    assert _ids(response) == [
        spread.others_pending_pizza.id,
        spread.accepted_pizza.id,
        spread.pending_pizza.id,
    ]
    assert response.json()["total"] == 3


async def test_the_order_book_matches_the_equivalent_filter(
    client: AsyncClient, admin_token: str, spread: Spread, other_restaurant: Restaurant
) -> None:
    book = await _get(
        client,
        admin_token,
        f"/admin/restaurants/{other_restaurant.id}/orders",
        limit=100,
    )
    filtered = await _get(
        client,
        admin_token,
        "/admin/orders",
        restaurant_id=other_restaurant.id,
        limit=100,
    )
    assert book.json() == filtered.json()


async def test_the_order_book_is_paginated_newest_first(
    client: AsyncClient, admin_token: str, spread: Spread, restaurant: Restaurant
) -> None:
    first = await _get(client, admin_token, f"/admin/restaurants/{restaurant.id}/orders", limit=2)
    second = await _get(
        client,
        admin_token,
        f"/admin/restaurants/{restaurant.id}/orders",
        limit=2,
        offset=2,
    )
    assert _ids(first) == [spread.others_pending_pizza.id, spread.accepted_pizza.id]
    assert _ids(second) == [spread.pending_pizza.id]
    assert first.json()["total"] == second.json()["total"] == 3


async def test_a_restaurant_with_no_orders_is_an_empty_page(
    client: AsyncClient, admin_token: str, other_restaurant: Restaurant
) -> None:
    response = await _get(client, admin_token, f"/admin/restaurants/{other_restaurant.id}/orders")
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


async def test_an_unknown_restaurant_is_404(client: AsyncClient, admin_token: str) -> None:
    response = await _get(client, admin_token, "/admin/restaurants/10000000/orders")
    assert response.status_code == 404


async def test_an_inactive_restaurant_still_has_an_order_book(
    client: AsyncClient,
    db_session: Session,
    admin_token: str,
    spread: Spread,
    restaurant: Restaurant,
) -> None:
    # Closing a restaurant hides it from the public listing; its history does
    # not disappear with it, and an admin still needs to read it.
    restaurant.is_active = False
    db_session.commit()

    response = await _get(
        client, admin_token, f"/admin/restaurants/{restaurant.id}/orders", limit=100
    )
    assert response.status_code == 200
    assert response.json()["total"] == 3


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
async def test_invalid_order_book_paging_values_are_422(
    client: AsyncClient,
    admin_token: str,
    restaurant: Restaurant,
    params: dict[str, object],
) -> None:
    response = await _get(
        client, admin_token, f"/admin/restaurants/{restaurant.id}/orders", **params
    )
    assert response.status_code == 422


# --- an order placed for real shows up -----------------------------------


async def test_an_order_placed_over_http_appears_in_every_admin_view(
    client: AsyncClient,
    admin_token: str,
    user_token: str,
    user: User,
    restaurant: Restaurant,
    margherita: RestaurantItem,
) -> None:
    placed = await client.post(
        "/orders",
        headers=auth_header(user_token),
        json={
            "restaurant_id": restaurant.id,
            "delivery_address": "10 Customer Lane",
            "items": [{"restaurant_item_id": margherita.id, "quantity": 2}],
        },
    )
    assert placed.status_code == 201
    order_id = placed.json()["id"]

    everywhere = [
        await _get(client, admin_token, "/admin/orders", limit=100),
        await _get(client, admin_token, "/admin/orders", customer_id=user.id),
        await _get(
            client,
            admin_token,
            "/admin/orders",
            status=OrderStatus.pending.value,
            limit=100,
        ),
        await _get(client, admin_token, f"/admin/restaurants/{restaurant.id}/orders", limit=100),
    ]
    for response in everywhere:
        assert order_id in _ids(response), response.text
