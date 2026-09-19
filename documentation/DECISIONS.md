# Decisions

The fixed skeleton of this project: the data model, how a request is authenticated, the
conventions every endpoint follows, and what is deliberately not built.

This file is the starting point to build *on*. It states what is settled, not why each
milestone went the way it did — that is [PLAN.md](PLAN.md) for the spec and the README for the
delivered behaviour.

## Stack

FastAPI · PostgreSQL 17 · SQLAlchemy 2.0 · Alembic · Pydantic v2 · custom JWT (PyJWT + pwdlib) ·
Gunicorn with `uvicorn_worker.UvicornWorker` behind nginx · Docker Compose.

Every dependency is `==`-pinned in [pyproject.toml](../pyproject.toml) and resolved in
[requirements.lock](../requirements.lock).

## Data model

Five tables. SQLAlchemy 2.0 declarative style throughout — `Mapped[...]` + `mapped_column(...)`,
never the old `Column()` form.

| Table | Columns |
| --- | --- |
| `users` | `id`, `email` (unique, indexed), `hashed_password`, `full_name`, `role`, `is_active`, `created_at` |
| `restaurants` | `id`, `name`, `address`, `phone` (nullable), `is_active` |
| `restaurant_items` | `id`, `restaurant_id` → `restaurants.id`, `name`, `description` (nullable), `price` `Numeric(10,2)`, `is_available` |
| `orders` | `id`, `customer_id` → `users.id`, `restaurant_id` → `restaurants.id`, `status`, `delivery_address`, `total_amount` `Numeric(10,2)`, `created_at`, `updated_at` |
| `order_items` | `id`, `order_id` → `orders.id`, `restaurant_item_id` → `restaurant_items.id`, `quantity`, `unit_price` `Numeric(10,2)` |

Relations, all one-to-many:

```
restaurants  1─N  restaurant_items  1─N  order_items
restaurants  1─N  orders            1─N  order_items
users        1─N  orders
```

- Money is `Numeric(10, 2)`, never `Float`.
- Timestamps are `DateTime(timezone=True)` with `server_default=func.now()`; `orders.updated_at`
  also carries `onupdate=func.now()`, so that clock has exactly one definition.
- `app/db/base.py` imports every model below `Base`, which is what makes Alembic autogenerate see
  all five tables.

## Enums

Both are `(str, Enum)` and both become native Postgres enum types via
`Enum(..., name="user_role" | "order_status")`.

- `UserRole`: `user`, `admin`.
- `OrderStatus`: `pending`, `accepted`, `out_for_delivery`, `delivered`.

## Invariants

- **Status moves one step forward.** `pending → accepted → out_for_delivery → delivered`.
  `delivered` is terminal, there is no cancellation, and every other move is refused with 409.
  The table lives in `app/services/transitions.py`.
- **`order_items.unit_price` is a snapshot** taken when the order is placed. Re-pricing a dish
  never restates what a customer was already charged, and `orders.total_amount` is never
  recomputed from today's menu.
- **Availability is a flag**, `restaurant_items.is_available`. No stock counts, no
  ingredient-level inventory.
- **Registration never grants admin.** Roles change only through `PATCH /admin/users/{id}`.

## Auth

Custom JWT, **not** `fastapi-users` — the default FastAPI approach, minimal dependencies, and
full control over the token payload, expiry and role checks.

- Token extraction: `OAuth2PasswordBearer(tokenUrl="/auth/login")`, so `POST /auth/login`
  consumes `OAuth2PasswordRequestForm` and its `username` field carries the email.
- Tokens: PyJWT, HS256, decoded with an explicit `algorithms=["HS256"]` allowlist. Claims are
  `sub` (user id as a string), `role`, `exp`, `iat`.
- Access tokens are short-lived (15–30 min). No refresh tokens, no revocation list, no password
  reset. A deactivated account is refused on its next request because `get_current_user`
  re-reads `is_active`.
- Passwords: one-way Argon2 hashing via pwdlib. No schema ever serialises the hash.
- Roles: two, `user` and `admin`.
- Dependencies in `app/api/deps.py`, exposed as `Annotated` aliases so no router signature ever
  spells out `Depends(...)` inline: `CurrentUser`, `AdminUser`, `OptionalUser`, `DbSession`.

## API conventions

- **Status codes:** 401 authentication, 403 authorization, 404 unknown id, 400 business rule,
  409 state conflict or duplicate, 422 malformed input.
- **Errors:** services raise from the `AppError` hierarchy in `app/core/errors.py` and stay free
  of `fastapi`; handlers registered in the app factory render `{"detail": "..."}`.
- **PATCH bodies** are all-optional schemas applied with `model_dump(exclude_unset=True)`. `None`
  means "field absent", so an explicit `null` on a NOT NULL column is 422, never 500.
- **Listings** order by `created_at DESC, id DESC`. The id tiebreaker is load-bearing: Postgres
  `now()` is the transaction clock, so rows written together share a timestamp.
- **Paging** is `limit` 1–100 (default 20) and `offset` ≥ 0. The `/admin` listings wrap their
  page in `PaginatedResponse[T]`; `GET /orders` and `GET /restaurants` return bare lists.

## Runtime shape

- **One command:** `docker compose up --build`. `docker/entrypoint.sh` runs `alembic upgrade head`
  and then `exec gunicorn`, under `set -e` — nothing manual sits between `up` and a working API.
- **nginx is the only ingress.** The `api` service publishes no host port; only nginx does, on
  8080. That is what makes Gunicorn's `--forwarded-allow-ips='*'` safe — if the API is ever
  published directly, the wildcard has to go in the same change.
- **The API image is two-stage** and the runtime stage carries no source tree: the builder runs
  `pip install . --no-deps` into `/opt/venv`, which the runtime copies along with `alembic.ini`
  and `alembic/`. `--no-deps` is what stops pip re-resolving past the lockfile.
- **Base images float on minor tags** (`python:3.12-slim`, `nginx:1-alpine`). Digest pinning was
  considered and left out: for a tech challenge, a compose file a reviewer can read at a glance
  is worth more than byte-exact image identity. A deployment that needs reproducible images pins
  digests here.

## Out of scope

Product features this project does not have and will not grow: payments, refunds, coupons,
driver assignment, GPS or live tracking, and any inventory beyond the availability flag.

Engineering gaps that were found, weighed and deliberately left unbuilt are listed under
[Known limitations](../README.md#known-limitations) in the README.
