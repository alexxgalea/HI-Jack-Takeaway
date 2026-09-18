"""Development seed data: one admin and two restaurants with full menus.

For local databases only. The admin password below is a placeholder chosen to
be typed quickly at a dev console, not a credential - nothing here should ever
run against an environment that holds real accounts.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.enums import UserRole
from app.models.restaurant import Restaurant
from app.models.restaurant_item import RestaurantItem
from app.models.user import User

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "admin123"
ADMIN_FULL_NAME = "Seed Admin"


@dataclass(frozen=True)
class ItemSpec:
    name: str
    description: str
    price: Decimal


@dataclass(frozen=True)
class RestaurantSpec:
    name: str
    address: str
    phone: str
    items: tuple[ItemSpec, ...]


# Addresses and phone numbers are invented: both columns are NOT NULL, and the
# task fixed only the names, items and prices.
RESTAURANTS: tuple[RestaurantSpec, ...] = (
    RestaurantSpec(
        name="Pizza Place",
        address="1 Napoli Street",
        phone="+40 700 100 100",
        items=(
            ItemSpec("Margherita", "Tomato, mozzarella, basil", Decimal("12.99")),
            ItemSpec("Pepperoni", "Tomato, mozzarella, pepperoni", Decimal("14.99")),
        ),
    ),
    RestaurantSpec(
        name="Burger Joint",
        address="42 Grill Avenue",
        phone="+40 700 200 200",
        items=(
            ItemSpec("Cheeseburger", "Beef patty, cheddar, pickles", Decimal("9.99")),
            ItemSpec("Fries", "Skin-on, sea salt", Decimal("3.99")),
        ),
    ),
)


def _seed_admin(db: Session) -> User:
    admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
    if admin is not None:
        return admin

    admin = User(
        email=ADMIN_EMAIL,
        hashed_password=hash_password(ADMIN_PASSWORD),
        full_name=ADMIN_FULL_NAME,
        role=UserRole.admin,
        is_active=True,
    )
    db.add(admin)
    db.flush()
    return admin


def _seed_restaurant(db: Session, spec: RestaurantSpec) -> Restaurant:
    restaurant = db.scalar(select(Restaurant).where(Restaurant.name == spec.name))
    if restaurant is None:
        restaurant = Restaurant(
            name=spec.name,
            address=spec.address,
            phone=spec.phone,
            is_active=True,
        )
        db.add(restaurant)
        db.flush()  # the items below need the generated id

    already_on_the_menu = set(
        db.scalars(
            select(RestaurantItem.name).where(
                RestaurantItem.restaurant_id == restaurant.id
            )
        )
    )
    for item in spec.items:
        if item.name in already_on_the_menu:
            continue
        db.add(
            RestaurantItem(
                restaurant_id=restaurant.id,
                name=item.name,
                description=item.description,
                price=item.price,
                is_available=True,
            )
        )
    return restaurant


def seed(db: Session) -> None:
    """Create the admin, the restaurants and their menus, then commit.

    Idempotent by name and email: a second run adds nothing and raises
    nothing, so it is safe to re-run after a migration without first
    dropping the database.
    """
    _seed_admin(db)
    for spec in RESTAURANTS:
        _seed_restaurant(db, spec)
    db.commit()
