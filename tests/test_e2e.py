"""One journey through the whole API, against the real Postgres test database.

Deliberately not a second copy of the unit suite: the edge cases belong to
`test_orders.py` and `test_transitions.py`, and repeating them here would only
give every future change two places to fail. What this covers is the seam the
focused tests cannot - that a token minted by `POST /auth/login` is accepted by
`POST /orders`, that the id handed back by one endpoint is the id the next one
wants, and that an order placed over HTTP can be walked to `delivered` and read
back in that state.
"""

from decimal import Decimal

from httpx import AsyncClient

from tests.test_auth import auth_header

CUSTOMER_EMAIL = "e2e-customer@example.com"
CUSTOMER_PASSWORD = "a-good-password"


async def test_register_login_browse_order_and_delivery(
    client: AsyncClient, admin_token: str
) -> None:
    admin = auth_header(admin_token)

    # --- an admin opens a restaurant and puts two dishes on the menu ------
    restaurant = await client.post(
        "/restaurants",
        json={"name": "E2E Pizza", "address": "1 Napoli Street"},
        headers=admin,
    )
    assert restaurant.status_code == 201
    restaurant_id = restaurant.json()["id"]

    prices = {"Margherita": Decimal("12.99"), "Pepperoni": Decimal("14.99")}
    menu: dict[str, int] = {}
    for name, price in prices.items():
        item = await client.post(
            f"/restaurants/{restaurant_id}/items",
            json={"name": name, "price": str(price)},
            headers=admin,
        )
        assert item.status_code == 201
        menu[name] = item.json()["id"]

    # --- a customer registers and logs in ---------------------------------
    registered = await client.post(
        "/auth/register",
        json={
            "email": CUSTOMER_EMAIL,
            "password": CUSTOMER_PASSWORD,
            "full_name": "E2E Customer",
        },
    )
    assert registered.status_code == 201
    assert registered.json()["role"] == "user"

    login = await client.post(
        "/auth/login",
        data={"username": CUSTOMER_EMAIL, "password": CUSTOMER_PASSWORD},
    )
    assert login.status_code == 200
    customer = auth_header(login.json()["access_token"])

    # --- browses, with no token at all ------------------------------------
    listing = await client.get("/restaurants")
    assert listing.status_code == 200
    assert restaurant_id in [row["id"] for row in listing.json()]

    items = await client.get(f"/restaurants/{restaurant_id}/items")
    assert items.status_code == 200
    assert {row["name"] for row in items.json()} == set(prices)

    # --- and orders two Margheritas and a Pepperoni -----------------------
    placed = await client.post(
        "/orders",
        json={
            "restaurant_id": restaurant_id,
            "delivery_address": "12 Customer Lane",
            "items": [
                {"restaurant_item_id": menu["Margherita"], "quantity": 2},
                {"restaurant_item_id": menu["Pepperoni"], "quantity": 1},
            ],
        },
        headers=customer,
    )
    assert placed.status_code == 201
    order = placed.json()
    order_id = order["id"]
    assert order["status"] == "pending"
    assert Decimal(order["total_amount"]) == prices["Margherita"] * 2 + prices[
        "Pepperoni"
    ]

    # --- the admin walks it down the pipeline, one legal step at a time ---
    for target in ("accepted", "out_for_delivery", "delivered"):
        moved = await client.patch(
            f"/orders/{order_id}/status", json={"status": target}, headers=admin
        )
        assert moved.status_code == 200
        assert moved.json()["status"] == target

    # --- and it reads back delivered, to the customer and to the admin ----
    reread = await client.get(f"/orders/{order_id}", headers=customer)
    assert reread.status_code == 200
    assert reread.json()["status"] == "delivered"
    assert Decimal(reread.json()["total_amount"]) == Decimal(order["total_amount"])

    book = await client.get(f"/admin/orders?restaurant_id={restaurant_id}", headers=admin)
    assert book.status_code == 200
    assert [(row["id"], row["status"]) for row in book.json()["items"]] == [
        (order_id, "delivered")
    ]

    # The pipeline is one-way: `delivered` is the end of the line.
    refused = await client.patch(
        f"/orders/{order_id}/status", json={"status": "pending"}, headers=admin
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]
