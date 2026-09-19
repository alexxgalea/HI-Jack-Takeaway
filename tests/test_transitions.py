from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.enums import OrderStatus
from app.models.order import Order
from app.models.restaurant import Restaurant
from app.models.restaurant_item import RestaurantItem
from app.services.transitions import ALLOWED_TRANSITIONS, can_transition
from tests.test_auth import auth_header

# The happy path in order, which is also the only way a test can drive an
# order into a given status: every step below is a legal one.
PIPELINE: tuple[OrderStatus, ...] = (
    OrderStatus.pending,
    OrderStatus.accepted,
    OrderStatus.out_for_delivery,
    OrderStatus.delivered,
)


@pytest.fixture
def restaurant(db_session: Session) -> Restaurant:
    restaurant = Restaurant(name="Pizza Place", address="1 Napoli Street")
    db_session.add(restaurant)
    db_session.commit()
    db_session.refresh(restaurant)
    return restaurant


@pytest.fixture
def margherita(db_session: Session, restaurant: Restaurant) -> RestaurantItem:
    item = RestaurantItem(
        restaurant_id=restaurant.id,
        name="Margherita",
        description=None,
        price=Decimal("12.99"),
        is_available=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


async def _place_order(
    client: AsyncClient, token: str, restaurant: Restaurant, item: RestaurantItem
) -> int:
    response = await client.post(
        "/orders",
        headers=auth_header(token),
        json={
            "restaurant_id": restaurant.id,
            "delivery_address": "10 Customer Lane",
            "items": [{"restaurant_item_id": item.id, "quantity": 1}],
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


async def _set_status(client: AsyncClient, token: str, order_id: int, target: OrderStatus):
    return await client.patch(
        f"/orders/{order_id}/status",
        headers=auth_header(token),
        json={"status": target.value},
    )


async def _drive_to(
    client: AsyncClient, admin_token: str, order_id: int, target: OrderStatus
) -> None:
    """Walk a freshly placed order up to `target` using only legal steps."""
    for step in PIPELINE[1 : PIPELINE.index(target) + 1]:
        response = await _set_status(client, admin_token, order_id, step)
        assert response.status_code == 200, response.text


@pytest.fixture
async def order_id(
    client: AsyncClient,
    user_token: str,
    admin_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
) -> int:
    # `admin_token` is requested here so the admin row exists before any test
    # body starts patching - the fixture order is otherwise incidental.
    return await _place_order(client, user_token, restaurant, margherita)


# --- the transition table itself ------------------------------------------


def test_the_table_covers_every_status() -> None:
    # A status missing from the table would be a dead end by accident rather
    # than by decision.
    assert set(ALLOWED_TRANSITIONS) == set(OrderStatus)


def test_delivered_is_terminal() -> None:
    assert ALLOWED_TRANSITIONS[OrderStatus.delivered] == set()


@pytest.mark.parametrize("current", list(OrderStatus))
@pytest.mark.parametrize("target", list(OrderStatus))
def test_can_transition_matches_the_table(current: OrderStatus, target: OrderStatus) -> None:
    assert can_transition(current, target) is (target in ALLOWED_TRANSITIONS[current])


def test_no_status_may_transition_to_itself() -> None:
    for state in OrderStatus:
        assert not can_transition(state, state)


# --- the endpoint, step by step -------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    list(zip(PIPELINE, PIPELINE[1:], strict=False)),
)
async def test_each_legal_step_returns_200_with_the_new_status(
    client: AsyncClient,
    admin_token: str,
    order_id: int,
    current: OrderStatus,
    target: OrderStatus,
) -> None:
    await _drive_to(client, admin_token, order_id, current)

    response = await _set_status(client, admin_token, order_id, target)
    assert response.status_code == 200
    assert response.json()["status"] == target.value


async def test_the_full_happy_path_ends_delivered(
    client: AsyncClient, admin_token: str, user_token: str, order_id: int
) -> None:
    await _drive_to(client, admin_token, order_id, OrderStatus.delivered)

    # The customer sees the walked status on their own order, not just the
    # admin's response echo.
    reread = await client.get(f"/orders/{order_id}", headers=auth_header(user_token))
    assert reread.json()["status"] == OrderStatus.delivered.value


async def test_skipping_a_step_is_409(client: AsyncClient, admin_token: str, order_id: int) -> None:
    response = await _set_status(client, admin_token, order_id, OrderStatus.delivered)
    assert response.status_code == 409


async def test_going_backwards_is_409(client: AsyncClient, admin_token: str, order_id: int) -> None:
    await _drive_to(client, admin_token, order_id, OrderStatus.delivered)

    response = await _set_status(client, admin_token, order_id, OrderStatus.pending)
    assert response.status_code == 409


# --- the full matrix through HTTP -----------------------------------------


@pytest.mark.parametrize("target", list(OrderStatus))
@pytest.mark.parametrize("current", list(OrderStatus))
async def test_the_endpoint_follows_the_transition_matrix(
    client: AsyncClient,
    db_session: Session,
    admin_token: str,
    order_id: int,
    current: OrderStatus,
    target: OrderStatus,
) -> None:
    await _drive_to(client, admin_token, order_id, current)

    response = await _set_status(client, admin_token, order_id, target)
    if can_transition(current, target):
        assert response.status_code == 200
        assert response.json()["status"] == target.value
    else:
        assert response.status_code == 409
        # A refused move leaves the order exactly where it was.
        assert response.json()["detail"]
        db_session.expire_all()
        assert db_session.get(Order, order_id).status == current


# --- authorization --------------------------------------------------------


async def test_non_admin_is_403(client: AsyncClient, user_token: str, order_id: int) -> None:
    # The owner of the order is still not the one who moves it.
    response = await _set_status(client, user_token, order_id, OrderStatus.accepted)
    assert response.status_code == 403


async def test_anonymous_is_401(client: AsyncClient, order_id: int) -> None:
    response = await client.patch(
        f"/orders/{order_id}/status",
        json={"status": OrderStatus.accepted.value},
    )
    assert response.status_code == 401


async def test_a_refused_move_does_not_write(
    client: AsyncClient, db_session: Session, user_token: str, order_id: int
) -> None:
    await _set_status(client, user_token, order_id, OrderStatus.accepted)

    db_session.expire_all()
    assert db_session.get(Order, order_id).status == OrderStatus.pending


# --- payload and target validation ----------------------------------------


async def test_a_status_outside_the_enum_is_422(
    client: AsyncClient, admin_token: str, order_id: int
) -> None:
    response = await client.patch(
        f"/orders/{order_id}/status",
        headers=auth_header(admin_token),
        json={"status": "cancelled"},
    )
    assert response.status_code == 422


async def test_an_empty_body_is_422(client: AsyncClient, admin_token: str, order_id: int) -> None:
    response = await client.patch(
        f"/orders/{order_id}/status", headers=auth_header(admin_token), json={}
    )
    assert response.status_code == 422


async def test_unknown_order_is_404(client: AsyncClient, admin_token: str) -> None:
    response = await _set_status(client, admin_token, 10_000_000, OrderStatus.accepted)
    assert response.status_code == 404


# --- the bookkeeping around a move ----------------------------------------


async def test_a_move_bumps_updated_at_and_leaves_the_rest_alone(
    client: AsyncClient, admin_token: str, order_id: int
) -> None:
    before = (await client.get(f"/orders/{order_id}", headers=auth_header(admin_token))).json()

    moved = await _set_status(client, admin_token, order_id, OrderStatus.accepted)
    after = moved.json()

    assert after["updated_at"] >= before["updated_at"]
    assert after["created_at"] == before["created_at"]
    # Advancing an order re-prices nothing and re-routes nothing.
    assert after["total_amount"] == before["total_amount"]
    assert after["delivery_address"] == before["delivery_address"]
    assert after["customer_id"] == before["customer_id"]
    assert after["items"] == before["items"]
