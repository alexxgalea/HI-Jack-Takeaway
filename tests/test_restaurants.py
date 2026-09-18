from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.restaurant import Restaurant
from app.models.restaurant_item import RestaurantItem
from app.schemas.restaurant import RestaurantOut
from app.schemas.restaurant_item import ItemOut
from tests.test_auth import auth_header

# M3 adds no conftest fixtures: the plan fixes that list, so the menu the
# tests browse is built here.


@pytest.fixture
def restaurant(db_session: Session) -> Restaurant:
    restaurant = Restaurant(
        name="Trattoria", address="1 Via Roma", phone="+40 700 000 000"
    )
    db_session.add(restaurant)
    db_session.commit()
    db_session.refresh(restaurant)
    return restaurant


@pytest.fixture
def available_item(db_session: Session, restaurant: Restaurant) -> RestaurantItem:
    item = RestaurantItem(
        restaurant_id=restaurant.id,
        name="Margherita",
        description="Tomato, mozzarella, basil",
        price=Decimal("32.50"),
        is_available=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


@pytest.fixture
def unavailable_item(db_session: Session, restaurant: Restaurant) -> RestaurantItem:
    item = RestaurantItem(
        restaurant_id=restaurant.id,
        name="Tartufo",
        description="Off the menu until the season starts",
        price=Decimal("88.00"),
        is_available=False,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


# --- anonymous browsing ---------------------------------------------------


async def test_anonymous_can_list_restaurants(
    client: AsyncClient, restaurant: Restaurant
) -> None:
    response = await client.get("/restaurants")
    assert response.status_code == 200

    listed = {r["id"]: r for r in response.json()}
    assert restaurant.id in listed
    assert listed[restaurant.id]["name"] == "Trattoria"
    assert set(listed[restaurant.id]) == set(RestaurantOut.model_fields)


async def test_inactive_restaurant_not_in_public_listing(
    client: AsyncClient, db_session: Session, restaurant: Restaurant
) -> None:
    closed = Restaurant(
        name="Closed Down", address="9 Shut Street", phone=None, is_active=False
    )
    db_session.add(closed)
    db_session.commit()
    db_session.refresh(closed)

    response = await client.get("/restaurants")
    assert response.status_code == 200

    listed = {r["id"] for r in response.json()}
    assert restaurant.id in listed
    assert closed.id not in listed


async def test_anonymous_can_read_one_restaurant(
    client: AsyncClient, restaurant: Restaurant
) -> None:
    response = await client.get(f"/restaurants/{restaurant.id}")
    assert response.status_code == 200
    assert response.json()["id"] == restaurant.id
    assert response.json()["address"] == "1 Via Roma"


async def test_anonymous_menu_lists_available_items_only(
    client: AsyncClient,
    restaurant: Restaurant,
    available_item: RestaurantItem,
    unavailable_item: RestaurantItem,
) -> None:
    response = await client.get(f"/restaurants/{restaurant.id}/items")
    assert response.status_code == 200

    body = response.json()
    assert [item["id"] for item in body] == [available_item.id]
    assert unavailable_item.name not in response.text
    assert set(body[0]) == set(ItemOut.model_fields)
    assert body[0]["price"] == "32.50"
    assert body[0]["is_available"] is True


async def test_menu_of_a_restaurant_without_items_is_empty_not_missing(
    client: AsyncClient, restaurant: Restaurant
) -> None:
    response = await client.get(f"/restaurants/{restaurant.id}/items")
    assert response.status_code == 200
    assert response.json() == []


# --- include_unavailable is admin-gated -----------------------------------


async def test_include_unavailable_without_a_token_returns_401(
    client: AsyncClient, restaurant: Restaurant, unavailable_item: RestaurantItem
) -> None:
    response = await client.get(
        f"/restaurants/{restaurant.id}/items", params={"include_unavailable": "true"}
    )
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert unavailable_item.name not in response.text


async def test_include_unavailable_as_a_plain_user_returns_403(
    client: AsyncClient,
    restaurant: Restaurant,
    unavailable_item: RestaurantItem,
    user_token: str,
) -> None:
    response = await client.get(
        f"/restaurants/{restaurant.id}/items",
        params={"include_unavailable": "true"},
        headers=auth_header(user_token),
    )
    assert response.status_code == 403
    assert unavailable_item.name not in response.text


async def test_include_unavailable_with_an_expired_token_returns_401(
    client: AsyncClient, restaurant: Restaurant, expired_token: str
) -> None:
    response = await client.get(
        f"/restaurants/{restaurant.id}/items",
        params={"include_unavailable": "true"},
        headers=auth_header(expired_token),
    )
    assert response.status_code == 401


async def test_include_unavailable_as_admin_returns_the_whole_menu(
    client: AsyncClient,
    restaurant: Restaurant,
    available_item: RestaurantItem,
    unavailable_item: RestaurantItem,
    admin_token: str,
) -> None:
    response = await client.get(
        f"/restaurants/{restaurant.id}/items",
        params={"include_unavailable": "true"},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [
        available_item.id,
        unavailable_item.id,
    ]


async def test_an_admin_without_the_flag_still_sees_the_public_menu(
    client: AsyncClient,
    restaurant: Restaurant,
    available_item: RestaurantItem,
    unavailable_item: RestaurantItem,
    admin_token: str,
) -> None:
    """The flag opts in; carrying an admin token does not widen the default."""
    response = await client.get(
        f"/restaurants/{restaurant.id}/items", headers=auth_header(admin_token)
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [available_item.id]


# --- writes are admin-only ------------------------------------------------


def _write_requests(restaurant_id: int, item_id: int) -> list[tuple[str, str, dict]]:
    return [
        ("POST", "/restaurants", {"name": "New", "address": "Somewhere"}),
        ("PATCH", f"/restaurants/{restaurant_id}", {"name": "Renamed"}),
        (
            "POST",
            f"/restaurants/{restaurant_id}/items",
            {"name": "Calzone", "price": "40.00"},
        ),
        ("PATCH", f"/items/{item_id}", {"is_available": False}),
    ]


async def test_writes_by_an_anonymous_caller_return_401(
    client: AsyncClient, restaurant: Restaurant, available_item: RestaurantItem
) -> None:
    for method, url, payload in _write_requests(restaurant.id, available_item.id):
        response = await client.request(method, url, json=payload)
        assert response.status_code == 401, f"{method} {url}"
        assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_writes_by_a_non_admin_return_403(
    client: AsyncClient,
    restaurant: Restaurant,
    available_item: RestaurantItem,
    user_token: str,
) -> None:
    for method, url, payload in _write_requests(restaurant.id, available_item.id):
        response = await client.request(
            method, url, json=payload, headers=auth_header(user_token)
        )
        assert response.status_code == 403, f"{method} {url}"


async def test_a_refused_write_changes_nothing(
    client: AsyncClient,
    db_session: Session,
    restaurant: Restaurant,
    user_token: str,
) -> None:
    await client.patch(
        f"/restaurants/{restaurant.id}",
        json={"name": "Hijacked"},
        headers=auth_header(user_token),
    )
    db_session.refresh(restaurant)
    assert restaurant.name == "Trattoria"


# --- admin writes ---------------------------------------------------------


async def test_admin_creates_a_restaurant(
    client: AsyncClient, db_session: Session, admin_token: str
) -> None:
    response = await client.post(
        "/restaurants",
        json={"name": "Sushi Bar", "address": "12 Dock St", "phone": "+40 711 111 111"},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 201

    body = response.json()
    assert body["name"] == "Sushi Bar"
    assert body["is_active"] is True
    assert db_session.get(Restaurant, body["id"]) is not None


async def test_admin_patches_a_restaurant_and_leaves_absent_fields_alone(
    client: AsyncClient, db_session: Session, restaurant: Restaurant, admin_token: str
) -> None:
    response = await client.patch(
        f"/restaurants/{restaurant.id}",
        json={"name": "Trattoria Nuova"},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Trattoria Nuova"
    assert response.json()["address"] == "1 Via Roma"
    assert response.json()["phone"] == "+40 700 000 000"

    db_session.refresh(restaurant)
    assert restaurant.name == "Trattoria Nuova"


async def test_admin_can_deactivate_a_restaurant(
    client: AsyncClient, restaurant: Restaurant, admin_token: str
) -> None:
    response = await client.patch(
        f"/restaurants/{restaurant.id}",
        json={"is_active": False},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False


async def test_admin_adds_an_item_to_a_menu(
    client: AsyncClient, db_session: Session, restaurant: Restaurant, admin_token: str
) -> None:
    response = await client.post(
        f"/restaurants/{restaurant.id}/items",
        json={"name": "Calzone", "description": "Folded", "price": "44.90"},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 201

    body = response.json()
    assert body["restaurant_id"] == restaurant.id
    assert body["price"] == "44.90"
    assert body["is_available"] is True

    stored = db_session.get(RestaurantItem, body["id"])
    assert stored is not None
    assert stored.price == Decimal("44.90")


async def test_admin_patch_toggles_item_availability_both_ways(
    client: AsyncClient,
    db_session: Session,
    available_item: RestaurantItem,
    restaurant: Restaurant,
    admin_token: str,
) -> None:
    withdrawn = await client.patch(
        f"/items/{available_item.id}",
        json={"is_available": False},
        headers=auth_header(admin_token),
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["is_available"] is False

    public = await client.get(f"/restaurants/{restaurant.id}/items")
    assert public.json() == []

    restored = await client.patch(
        f"/items/{available_item.id}",
        json={"is_available": True},
        headers=auth_header(admin_token),
    )
    assert restored.status_code == 200
    assert restored.json()["is_available"] is True

    public = await client.get(f"/restaurants/{restaurant.id}/items")
    assert [item["id"] for item in public.json()] == [available_item.id]


async def test_admin_patches_an_item_price_without_touching_its_name(
    client: AsyncClient, db_session: Session, available_item: RestaurantItem, admin_token: str
) -> None:
    response = await client.patch(
        f"/items/{available_item.id}",
        json={"price": "35.00"},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 200
    assert response.json()["price"] == "35.00"
    assert response.json()["name"] == "Margherita"

    db_session.refresh(available_item)
    assert available_item.price == Decimal("35.00")


# --- unknown ids ----------------------------------------------------------


async def test_unknown_restaurant_id_returns_404_on_every_route(
    client: AsyncClient, admin_token: str
) -> None:
    missing = 10_000_000
    header = auth_header(admin_token)

    assert (await client.get(f"/restaurants/{missing}")).status_code == 404
    assert (await client.get(f"/restaurants/{missing}/items")).status_code == 404
    assert (
        await client.patch(
            f"/restaurants/{missing}", json={"name": "Ghost"}, headers=header
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/restaurants/{missing}/items",
            json={"name": "Ghost", "price": "1.00"},
            headers=header,
        )
    ).status_code == 404


async def test_unknown_item_id_returns_404(
    client: AsyncClient, admin_token: str
) -> None:
    response = await client.patch(
        "/items/10000000",
        json={"is_available": False},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 404


async def test_a_non_numeric_id_is_a_422_not_a_404(client: AsyncClient) -> None:
    assert (await client.get("/restaurants/not-an-id")).status_code == 422


# --- validation -----------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Free lunch", "price": "0"},
        {"name": "Refund", "price": "-1.00"},
        {"name": "Too precise", "price": "1.005"},
        {"name": "", "price": "10.00"},
        {"price": "10.00"},
    ],
)
async def test_item_payloads_that_the_schema_refuses(
    client: AsyncClient, restaurant: Restaurant, admin_token: str, payload: dict
) -> None:
    response = await client.post(
        f"/restaurants/{restaurant.id}/items",
        json=payload,
        headers=auth_header(admin_token),
    )
    assert response.status_code == 422


async def test_a_restaurant_without_a_name_is_refused(
    client: AsyncClient, admin_token: str
) -> None:
    response = await client.post(
        "/restaurants", json={"address": "Nowhere"}, headers=auth_header(admin_token)
    )
    assert response.status_code == 422


# --- conventions ----------------------------------------------------------


async def test_item_responses_never_expose_the_restaurant_relationship(
    client: AsyncClient, restaurant: Restaurant, available_item: RestaurantItem
) -> None:
    """ItemOut carries the FK, not a nested restaurant or order history."""
    body = (await client.get(f"/restaurants/{restaurant.id}/items")).json()[0]
    assert "restaurant" not in body
    assert "order_items" not in body
