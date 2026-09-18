from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, DbSession, OptionalUser, credentials_exception
from app.models.enums import UserRole
from app.models.restaurant import Restaurant
from app.models.restaurant_item import RestaurantItem
from app.models.user import User
from app.schemas.restaurant import RestaurantCreate, RestaurantOut, RestaurantUpdate
from app.schemas.restaurant_item import ItemCreate, ItemOut, ItemUpdate

# `PATCH /items/{id}` lives outside the /restaurants prefix, so this module
# exports two routers; main.py wires both.
router = APIRouter(prefix="/restaurants", tags=["restaurants"])
items_router = APIRouter(prefix="/items", tags=["restaurants"])


def _get_restaurant(db: Session, restaurant_id: int) -> Restaurant:
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Restaurant not found"
        )
    return restaurant


def _get_item(db: Session, item_id: int) -> RestaurantItem:
    item = db.get(RestaurantItem, item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Item not found"
        )
    return item


def _assert_may_see_unavailable(user: User | None) -> None:
    """Gate on `?include_unavailable=true`.

    The menu itself is public; only the view that exposes withdrawn dishes is
    an admin one. Anonymous is an authentication failure (401), a signed-in
    non-admin an authorization one (403).
    """
    if user is None:
        raise credentials_exception()
    if user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )


def _apply_patch(
    instance: Restaurant | RestaurantItem,
    payload: RestaurantUpdate | ItemUpdate,
) -> None:
    """Copy only the fields the caller actually sent onto the row."""
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(instance, field, value)


# --- public ---------------------------------------------------------------


@router.get("", response_model=list[RestaurantOut])
def list_restaurants(db: DbSession) -> Sequence[Restaurant]:
    return db.scalars(select(Restaurant).order_by(Restaurant.id)).all()


@router.get("/{restaurant_id}", response_model=RestaurantOut)
def get_restaurant(restaurant_id: int, db: DbSession) -> Restaurant:
    return _get_restaurant(db, restaurant_id)


@router.get("/{restaurant_id}/items", response_model=list[ItemOut])
def list_restaurant_items(
    restaurant_id: int,
    db: DbSession,
    user: OptionalUser,
    include_unavailable: bool = False,
) -> Sequence[RestaurantItem]:
    if include_unavailable:
        _assert_may_see_unavailable(user)

    _get_restaurant(db, restaurant_id)

    stmt = select(RestaurantItem).where(RestaurantItem.restaurant_id == restaurant_id)
    if not include_unavailable:
        stmt = stmt.where(RestaurantItem.is_available.is_(True))
    return db.scalars(stmt.order_by(RestaurantItem.id)).all()


# --- admin ----------------------------------------------------------------


@router.post("", response_model=RestaurantOut, status_code=status.HTTP_201_CREATED)
def create_restaurant(
    payload: RestaurantCreate, db: DbSession, admin: AdminUser
) -> Restaurant:
    restaurant = Restaurant(**payload.model_dump())
    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)
    return restaurant


@router.patch("/{restaurant_id}", response_model=RestaurantOut)
def update_restaurant(
    restaurant_id: int, payload: RestaurantUpdate, db: DbSession, admin: AdminUser
) -> Restaurant:
    restaurant = _get_restaurant(db, restaurant_id)
    _apply_patch(restaurant, payload)
    db.commit()
    db.refresh(restaurant)
    return restaurant


@router.post(
    "/{restaurant_id}/items", response_model=ItemOut, status_code=status.HTTP_201_CREATED
)
def create_restaurant_item(
    restaurant_id: int, payload: ItemCreate, db: DbSession, admin: AdminUser
) -> RestaurantItem:
    _get_restaurant(db, restaurant_id)
    item = RestaurantItem(restaurant_id=restaurant_id, **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@items_router.patch("/{item_id}", response_model=ItemOut)
def update_item(
    item_id: int, payload: ItemUpdate, db: DbSession, admin: AdminUser
) -> RestaurantItem:
    # `is_available` is an ordinary field here: withdrawing a dish from the
    # menu is the same PATCH as renaming it.
    item = _get_item(db, item_id)
    _apply_patch(item, payload)
    db.commit()
    db.refresh(item)
    return item
