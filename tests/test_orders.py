from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.models.enums import OrderStatus, UserRole
from app.models.order import Order
from app.models.restaurant import Restaurant
from app.models.restaurant_item import RestaurantItem
from app.models.user import User
from app.schemas.order import OrderItemOut, OrderOut
from tests.test_auth import auth_header

# The plan fixes the conftest fixture list, so the menu these tests order
# against - and the second customer they read across - are built here.


@pytest.fixture
def restaurant(db_session: Session) -> Restaurant:
    restaurant = Restaurant(name="Pizza Place", address="1 Napoli Street")
    db_session.add(restaurant)
    db_session.commit()
    db_session.refresh(restaurant)
    return restaurant


@pytest.fixture
def other_restaurant(db_session: Session) -> Restaurant:
    restaurant = Restaurant(name="Burger Joint", address="42 Grill Avenue")
    db_session.add(restaurant)
    db_session.commit()
    db_session.refresh(restaurant)
    return restaurant


def _add_item(
    db: Session,
    restaurant: Restaurant,
    name: str,
    price: str,
    *,
    is_available: bool = True,
) -> RestaurantItem:
    item = RestaurantItem(
        restaurant_id=restaurant.id,
        name=name,
        description=None,
        price=Decimal(price),
        is_available=is_available,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@pytest.fixture
def margherita(db_session: Session, restaurant: Restaurant) -> RestaurantItem:
    return _add_item(db_session, restaurant, "Margherita", "12.99")


@pytest.fixture
def pepperoni(db_session: Session, restaurant: Restaurant) -> RestaurantItem:
    return _add_item(db_session, restaurant, "Pepperoni", "14.99")


@pytest.fixture
def sold_out(db_session: Session, restaurant: Restaurant) -> RestaurantItem:
    return _add_item(db_session, restaurant, "Tartufo", "88.00", is_available=False)


@pytest.fixture
def foreign_item(db_session: Session, other_restaurant: Restaurant) -> RestaurantItem:
    return _add_item(db_session, other_restaurant, "Cheeseburger", "9.99")


@pytest.fixture
def other_user(db_session: Session) -> User:
    user = User(
        email="other@example.com",
        hashed_password=hash_password("other-password"),
        full_name="Other Customer",
        role=UserRole.user,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def other_user_token(other_user: User) -> str:
    return create_access_token(subject=str(other_user.id), role=other_user.role)


async def _place_order(
    client: AsyncClient,
    token: str,
    restaurant: Restaurant,
    lines: list[tuple[RestaurantItem, int]],
    address: str = "10 Customer Lane",
):
    return await client.post(
        "/orders",
        headers=auth_header(token),
        json={
            "restaurant_id": restaurant.id,
            "delivery_address": address,
            "items": [
                {"restaurant_item_id": item.id, "quantity": quantity} for item, quantity in lines
            ],
        },
    )


# --- placing an order -----------------------------------------------------


async def test_valid_order_returns_201_with_items_and_summed_total(
    client: AsyncClient,
    user_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
    pepperoni: RestaurantItem,
) -> None:
    response = await _place_order(client, user_token, restaurant, [(margherita, 2), (pepperoni, 1)])
    assert response.status_code == 201

    body = response.json()
    assert set(body) == set(OrderOut.model_fields)
    assert body["restaurant_id"] == restaurant.id
    assert body["delivery_address"] == "10 Customer Lane"

    assert len(body["items"]) == 2
    assert set(body["items"][0]) == set(OrderItemOut.model_fields)
    priced = {line["restaurant_item_id"]: line for line in body["items"]}
    assert Decimal(priced[margherita.id]["unit_price"]) == Decimal("12.99")
    assert priced[margherita.id]["quantity"] == 2
    assert Decimal(priced[pepperoni.id]["unit_price"]) == Decimal("14.99")

    expected = sum(
        (Decimal(line["unit_price"]) * line["quantity"] for line in body["items"]),
        Decimal("0.00"),
    )
    assert expected == Decimal("40.97")  # 12.99 * 2 + 14.99
    assert Decimal(body["total_amount"]) == expected


async def test_new_orders_start_pending(
    client: AsyncClient,
    user_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
) -> None:
    response = await _place_order(client, user_token, restaurant, [(margherita, 1)])
    assert response.status_code == 201
    assert response.json()["status"] == OrderStatus.pending.value


async def test_order_is_owned_by_the_token_holder(
    client: AsyncClient,
    user: User,
    user_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
) -> None:
    # The customer comes from the token, never from the payload.
    response = await _place_order(client, user_token, restaurant, [(margherita, 1)])
    assert response.json()["customer_id"] == user.id


async def test_anonymous_cannot_place_an_order(
    client: AsyncClient, restaurant: Restaurant, margherita: RestaurantItem
) -> None:
    response = await client.post(
        "/orders",
        json={
            "restaurant_id": restaurant.id,
            "delivery_address": "10 Customer Lane",
            "items": [{"restaurant_item_id": margherita.id, "quantity": 1}],
        },
    )
    assert response.status_code == 401


# --- rejected baskets -----------------------------------------------------


async def test_item_from_a_different_restaurant_is_rejected(
    client: AsyncClient,
    user_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
    foreign_item: RestaurantItem,
) -> None:
    response = await _place_order(
        client, user_token, restaurant, [(margherita, 1), (foreign_item, 1)]
    )
    assert response.status_code == 400


async def test_unavailable_item_is_rejected(
    client: AsyncClient,
    user_token: str,
    restaurant: Restaurant,
    sold_out: RestaurantItem,
) -> None:
    response = await _place_order(client, user_token, restaurant, [(sold_out, 1)])
    assert response.status_code == 400


async def test_unknown_item_id_is_rejected(
    client: AsyncClient, user_token: str, restaurant: Restaurant
) -> None:
    response = await client.post(
        "/orders",
        headers=auth_header(user_token),
        json={
            "restaurant_id": restaurant.id,
            "delivery_address": "10 Customer Lane",
            "items": [{"restaurant_item_id": 10_000_000, "quantity": 1}],
        },
    )
    assert response.status_code == 400


async def test_inactive_restaurant_is_rejected(
    client: AsyncClient,
    db_session: Session,
    user_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
) -> None:
    restaurant.is_active = False
    db_session.commit()

    response = await _place_order(client, user_token, restaurant, [(margherita, 1)])
    assert response.status_code == 400


async def test_unknown_restaurant_is_404(client: AsyncClient, user_token: str) -> None:
    response = await client.post(
        "/orders",
        headers=auth_header(user_token),
        json={
            "restaurant_id": 10_000_000,
            "delivery_address": "10 Customer Lane",
            "items": [{"restaurant_item_id": 1, "quantity": 1}],
        },
    )
    assert response.status_code == 404


@pytest.mark.parametrize("quantity", [0, -1])
async def test_non_positive_quantity_is_unprocessable(
    client: AsyncClient,
    user_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
    quantity: int,
) -> None:
    response = await _place_order(client, user_token, restaurant, [(margherita, quantity)])
    assert response.status_code == 422


async def test_empty_basket_is_unprocessable(
    client: AsyncClient, user_token: str, restaurant: Restaurant
) -> None:
    response = await _place_order(client, user_token, restaurant, [])
    assert response.status_code == 422


async def test_a_rejected_basket_writes_no_order(
    client: AsyncClient,
    db_session: Session,
    user: User,
    user_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
    sold_out: RestaurantItem,
) -> None:
    # The good line comes first: if the order were written before the basket
    # was fully checked, a half-built row would survive the rejection.
    response = await _place_order(client, user_token, restaurant, [(margherita, 1), (sold_out, 1)])
    assert response.status_code == 400

    listing = await client.get("/orders", headers=auth_header(user_token))
    assert listing.json() == []


# --- price snapshots ------------------------------------------------------


async def test_changing_a_menu_price_does_not_alter_stored_totals(
    client: AsyncClient,
    db_session: Session,
    user_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
) -> None:
    placed = await _place_order(client, user_token, restaurant, [(margherita, 2)])
    order_id = placed.json()["id"]
    assert Decimal(placed.json()["total_amount"]) == Decimal("25.98")

    margherita.price = Decimal("99.99")
    db_session.commit()

    reread = await client.get(f"/orders/{order_id}", headers=auth_header(user_token))
    assert reread.status_code == 200
    assert Decimal(reread.json()["total_amount"]) == Decimal("25.98")
    assert Decimal(reread.json()["items"][0]["unit_price"]) == Decimal("12.99")


# --- reading orders -------------------------------------------------------


async def test_owner_can_read_own_order(
    client: AsyncClient,
    user_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
) -> None:
    placed = await _place_order(client, user_token, restaurant, [(margherita, 1)])
    order_id = placed.json()["id"]

    response = await client.get(f"/orders/{order_id}", headers=auth_header(user_token))
    assert response.status_code == 200
    assert response.json()["id"] == order_id


async def test_reading_another_users_order_as_non_admin_is_404(
    client: AsyncClient,
    user_token: str,
    other_user_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
) -> None:
    placed = await _place_order(client, user_token, restaurant, [(margherita, 1)])
    order_id = placed.json()["id"]

    response = await client.get(f"/orders/{order_id}", headers=auth_header(other_user_token))
    assert response.status_code == 404


async def test_admin_can_read_another_users_order(
    client: AsyncClient,
    user_token: str,
    admin_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
) -> None:
    placed = await _place_order(client, user_token, restaurant, [(margherita, 1)])
    order_id = placed.json()["id"]

    response = await client.get(f"/orders/{order_id}", headers=auth_header(admin_token))
    assert response.status_code == 200
    assert response.json()["id"] == order_id


async def test_unknown_order_is_404(client: AsyncClient, user_token: str) -> None:
    response = await client.get("/orders/10000000", headers=auth_header(user_token))
    assert response.status_code == 404


async def test_listing_returns_only_the_callers_own_orders(
    client: AsyncClient,
    user_token: str,
    other_user_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
) -> None:
    mine = await _place_order(client, user_token, restaurant, [(margherita, 1)])
    theirs = await _place_order(client, other_user_token, restaurant, [(margherita, 1)])

    response = await client.get("/orders", headers=auth_header(user_token))
    assert response.status_code == 200

    listed = [order["id"] for order in response.json()]
    assert listed == [mine.json()["id"]]
    assert theirs.json()["id"] not in listed


async def test_listing_is_paginated_newest_first(
    client: AsyncClient,
    user_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
) -> None:
    placed = [
        (await _place_order(client, user_token, restaurant, [(margherita, 1)])).json()["id"]
        for _ in range(3)
    ]
    newest_first = list(reversed(placed))

    first_page = await client.get("/orders", headers=auth_header(user_token), params={"limit": 2})
    assert [order["id"] for order in first_page.json()] == newest_first[:2]

    second_page = await client.get(
        "/orders", headers=auth_header(user_token), params={"limit": 2, "offset": 2}
    )
    assert [order["id"] for order in second_page.json()] == newest_first[2:]


async def test_anonymous_cannot_list_orders(client: AsyncClient) -> None:
    assert (await client.get("/orders")).status_code == 401


async def test_listing_carries_the_items(
    client: AsyncClient,
    user_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
    pepperoni: RestaurantItem,
) -> None:
    await _place_order(client, user_token, restaurant, [(margherita, 2), (pepperoni, 3)])

    response = await client.get("/orders", headers=auth_header(user_token))
    [order] = response.json()
    assert {line["restaurant_item_id"] for line in order["items"]} == {
        margherita.id,
        pepperoni.id,
    }


# --- persisted shape ------------------------------------------------------


async def test_order_rows_snapshot_price_and_quantity(
    client: AsyncClient,
    db_session: Session,
    user_token: str,
    restaurant: Restaurant,
    margherita: RestaurantItem,
    pepperoni: RestaurantItem,
) -> None:
    placed = await _place_order(client, user_token, restaurant, [(margherita, 2), (pepperoni, 1)])

    order = db_session.get(Order, placed.json()["id"])
    assert order is not None
    assert order.status == OrderStatus.pending
    assert order.total_amount == Decimal("40.97")
    assert {(line.restaurant_item_id, line.quantity, line.unit_price) for line in order.items} == {
        (margherita.id, 2, Decimal("12.99")),
        (pepperoni.id, 1, Decimal("14.99")),
    }
