# Implementation Plan

Constraints fixed by [DECISIONS.md](DECISIONS.md). Stack: FastAPI, PostgreSQL, SQLAlchemy 2.0, Alembic, Pydantic v2.

**Auth is locked:** custom JWT via `pyjwt` + `pwdlib`, no `fastapi-users`. Token extraction through `OAuth2PasswordBearer(tokenUrl="/auth/login")`, HS256 only, short-lived access tokens (15-30 min), role checks in hand-written dependencies.

## Target layout

```
app/
  main.py                # app factory, router wiring
  core/config.py         # Settings (pydantic-settings)
  core/security.py       # pwdlib hashing + pyjwt encode/decode
  db/base.py             # DeclarativeBase + model imports for Alembic
  db/session.py          # engine, SessionLocal, get_db
  models/                # user, restaurant, restaurant_item, order, order_item
  schemas/               # pydantic in/out per resource
  api/deps.py            # get_current_user, require_admin
  api/routers/           # auth, restaurants, orders, admin
  services/              # order_service, transitions
alembic/
tests/
```

---

## M0 - Scaffolding

- **Files:** `pyproject.toml`, `.env.example`, `docker-compose.yml` (postgres), `app/main.py`, `app/core/config.py`, `app/db/session.py`, `app/db/base.py`, `tests/conftest.py`
- **Key items:**
  - **Versions are frozen in `pyproject.toml`** - every dependency pinned with `==`, no open ranges, runtime and test alike. Resolve the exact patch versions against PyPI on the day M0 is built and commit a lockfile (`uv.lock` / `requirements.lock`) alongside.
  - Runtime deps: `fastapi`, `uvicorn[standard]`, `sqlalchemy`, `alembic`, `psycopg[binary]`, `pydantic`, `pydantic-settings`, `pyjwt`, `pwdlib[argon2]`, `python-multipart`
  - Test deps (`[dependency-groups] dev` or `[project.optional-dependencies] test`): `pytest`, `pytest-asyncio`, `httpx`, `pytest-cov`
  - **`python-multipart` is mandatory, not optional.** `OAuth2PasswordRequestForm` (M2, `POST /auth/login`) parses `multipart/form-data`; without this package FastAPI raises at import/route-definition time, and the `/docs` Authorize button cannot work. Easy to drop when trimming deps - it is never imported by name in application code.
  - `pytest-asyncio` in `asyncio_mode = "auto"`; `httpx.ASGITransport` drives `AsyncClient` against the app without a live server
  - `Settings` - `database_url`, `jwt_secret`, `jwt_algorithm` (default `"HS256"`), `access_token_expire_minutes` (default `30`); cached `get_settings()`
  - `.env.example` carries a placeholder `JWT_SECRET` with a note to generate per-environment; startup fails loudly if unset
  - `create_app() -> FastAPI`; `GET /health`
  - `engine`, `SessionLocal`, `get_db()` generator
  - `Base(DeclarativeBase)`
- **Acceptance:**
  - `uvicorn app.main:app` boots; `GET /health` returns `200 {"status":"ok"}`
  - `/docs` renders
  - Postgres reachable via compose; `pytest` runs with 0 failures
  - Every dependency in `pyproject.toml` is `==`-pinned; lockfile committed
  - A trivial `async def` test using `httpx.AsyncClient` passes, proving the async test wiring works before any endpoint depends on it

## M1 - Models + first migration

- **Files:** `app/models/{user,restaurant,restaurant_item,order,order_item,enums}.py`, `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial.py`
- **Key items:**
  - `UserRole(str, Enum)`: `user`, `admin`
  - `OrderStatus(str, Enum)`: `pending`, `accepted`, `out_for_delivery`, `delivered`
  - `User(id, email uq+idx, hashed_password, full_name, role, is_active, created_at)`
  - `Restaurant(id, name, address, phone, is_active)` + `items` relationship
  - `RestaurantItem(id, restaurant_id FK, name, description, price Numeric(10,2), is_available)`
  - `Order(id, customer_id FK, restaurant_id FK, status, delivery_address, total_amount, created_at, updated_at)` + `items` relationship
  - `OrderItem(id, order_id FK, restaurant_item_id FK, quantity, unit_price Numeric(10,2))`
  - `alembic/env.py` reads `Settings.database_url`, `target_metadata = Base.metadata`
- **Acceptance:**
  - `alembic upgrade head` creates 5 tables + enum types
  - `alembic downgrade base` leaves a clean DB
  - Autogenerate after upgrade produces an empty diff
  - FK + unique constraints visible in psql `\d`

## M2 - Auth (custom JWT, pyjwt + pwdlib)

- **Files:** `app/core/security.py`, `app/schemas/user.py`, `app/schemas/token.py`, `app/api/deps.py`, `app/api/routers/auth.py`, `tests/test_auth.py`, `tests/test_security.py`
- **`app/core/security.py`:**
  - `password_hash = PasswordHash.recommended()` (pwdlib, Argon2)
  - `hash_password(plain: str) -> str`
  - `verify_password(plain: str, hashed: str) -> bool`
  - `create_access_token(subject: str, role: UserRole, expires_delta: timedelta | None = None) -> str` - claims `sub` (user id as str), `role`, `exp`, `iat`; `jwt.encode(..., settings.jwt_secret, algorithm=settings.jwt_algorithm)`
  - `decode_token(token: str) -> TokenPayload` - `jwt.decode(..., algorithms=["HS256"])`; translates `ExpiredSignatureError` / `InvalidTokenError` into a single `CredentialsError`, never leaking which failed
- **`app/schemas/token.py`:** `Token(access_token, token_type="bearer")`, `TokenPayload(sub: str, role: UserRole, exp: int, iat: int)`
- **`app/schemas/user.py`:** `UserCreate(email: EmailStr, password: SecretStr, full_name)`, `UserOut(id, email, full_name, role, is_active)` - no hash field ever
- **`app/api/deps.py`:**
  - `oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")`
  - `get_current_user(token: Annotated[str, Depends(oauth2_scheme)], db) -> User` - decode -> validate `exp`/`sub` -> load user by id -> 401 (`WWW-Authenticate: Bearer`) on decode failure, missing user, or `is_active=False`
  - `require_admin(user: CurrentUser) -> User` - `user.role == UserRole.admin` else 403
  - Two module-level **type aliases**, the only form routers use:
    ```python
    from typing import Annotated
    from fastapi import Depends

    CurrentUser = Annotated[User, Depends(get_current_user)]
    AdminUser = Annotated[User, Depends(require_admin)]
    ```
    Routers then declare `user: CurrentUser` / `admin: AdminUser` - no inline `Depends(...)` in any signature past this milestone
- **`app/api/routers/auth.py`:**
  - `POST /auth/register` -> 201 `UserOut`, always role `user`
  - `POST /auth/login` -> `Token`; consumes `OAuth2PasswordRequestForm` (form fields `username`/`password`, where `username` is the email) so the path matches `tokenUrl` and the `/docs` Authorize button works
  - `GET /auth/me` -> `UserOut` for `CurrentUser`
- **Acceptance:**
  - Register returns 201; neither password nor hash appears in any response or log
  - Duplicate email -> 409
  - Wrong password -> 401; unknown email -> 401 with the identical body (no user enumeration)
  - Login response is `{"access_token": ..., "token_type": "bearer"}`; decoding it yields `sub`, `role`, `exp`, `iat`
  - `exp` is 15-30 min out, driven by `access_token_expire_minutes`
  - `/auth/me` -> 401 with no token, 401 with a malformed token, 401 with an expired token (frozen/back-dated `exp`), 200 with a valid one
  - Token signed with a different secret -> 401
  - Token with `alg: none` or `HS512` -> 401 (algorithm allowlist enforced)
  - Deactivated user's still-valid token -> 401
  - `require_admin` with a `user` token -> 403; with an `admin` token -> passes
  - `grep -r "Depends(get_current_user)\|Depends(require_admin)" app/api/routers/` returns nothing - routers use `CurrentUser` / `AdminUser` only
  - `hash_password` output is never the plaintext and differs across two calls on the same input; `verify_password` accepts both
  - `/docs` Authorize flow issues a working token end to end

## M3 - Restaurants & menu

- **Files:** `app/schemas/restaurant.py`, `app/schemas/restaurant_item.py`, `app/api/routers/restaurants.py`, `tests/test_restaurants.py`
- **Key items:**
  - Schemas: `RestaurantCreate/Update/Out`, `ItemCreate/Update/Out`
  - Public: `GET /restaurants`, `GET /restaurants/{id}`, `GET /restaurants/{id}/items` (available only; `?include_unavailable=true` admin-gated)
  - Admin: `POST /restaurants`, `PATCH /restaurants/{id}`, `POST /restaurants/{id}/items`, `PATCH /items/{id}` (also toggles `is_available`)
- **Acceptance:**
  - Anonymous can browse restaurants and available items
  - Non-admin write -> 403; admin write -> 201/200
  - Unknown id -> 404
  - Unavailable items absent from the public menu listing

## M4 - Order placement

- **Files:** `app/schemas/order.py`, `app/services/order_service.py`, `app/api/routers/orders.py`, `tests/test_orders.py`
- **Key items:**
  - `OrderItemIn(restaurant_item_id, quantity)`, `OrderCreate(restaurant_id, delivery_address, items)`, `OrderOut`, `OrderItemOut`
  - `create_order(db, customer, payload) -> Order` - validates restaurant is active, every item belongs to that restaurant, all `is_available`, `quantity > 0`; snapshots `unit_price`; computes `total_amount`; one transaction
  - `POST /orders`, `GET /orders` (own, paginated), `GET /orders/{id}` (owner or admin)
- **Acceptance:**
  - Valid order -> 201 with items and total = sum(unit_price * quantity)
  - Item from a different restaurant -> 400
  - Unavailable item -> 400
  - Changing a menu price afterwards does not alter stored order totals
  - Reading another user's order as non-admin -> 404
  - New orders start in `pending`

## M5 - Status transitions

- **Files:** `app/services/transitions.py`, extend `app/api/routers/orders.py`, `tests/test_transitions.py`
- **Key items:**
  - `ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]]` - `pending -> {accepted}`, `accepted -> {out_for_delivery}`, `out_for_delivery -> {delivered}`, `delivered -> {}`
  - `can_transition(current, target) -> bool`, `assert_transition(...)` raising 409
  - `set_order_status(db, order, target)` - row lock via `with_for_update`, bumps `updated_at`
  - `PATCH /orders/{id}/status` (admin only), body `{"status": ...}`
- **Acceptance:**
  - Each legal step -> 200 with the new status
  - Skipping a step (`pending -> delivered`) -> 409
  - Going backwards (`delivered -> pending`) -> 409
  - Non-admin -> 403
  - Parametrized test covers the full transition matrix

## M6 - Admin endpoints

- **Files:** `app/api/routers/admin.py`, `tests/test_admin.py`
- **Key items:**
  - `GET /admin/orders` - filters `status`, `restaurant_id`, `customer_id`, date range; `limit`/`offset`; newest first
  - `GET /admin/users`, `PATCH /admin/users/{id}` (role, `is_active`)
  - `GET /admin/restaurants/{id}/orders`
  - Shared `PaginatedResponse[T]` schema
- **Acceptance:**
  - Filters compose correctly; invalid filter values -> 422
  - Pagination metadata (`total`, `limit`, `offset`) accurate
  - Every `/admin/*` route rejects non-admin with 403

## M7 - Hardening

- **Files:** `app/core/errors.py`, `app/db/seed.py`, `tests/test_e2e.py`, `README.md`, `docker-compose.test.yml`, `.github/workflows/ci.yml`
- **Key items:**
  - `AppError` hierarchy (incl. `CredentialsError` raised from `decode_token`) + exception handlers -> consistent `{"detail": ...}` shape
  - Structured logging with `Authorization` headers and password fields redacted; CORS middleware
  - `seed()` - admin user, 2 restaurants, sample menus
  - E2E test: register -> login -> browse -> order -> admin walks status to `delivered`
  - `docker-compose.test.yml` - throwaway Postgres for CI and local test runs: separate service/volume name from `docker-compose.yml`, non-default host port, `tmpfs` data dir, healthcheck so CI waits for readiness instead of sleeping. Never points at the dev database.
  - CI job: `docker compose -f docker-compose.test.yml up -d --wait` -> `alembic upgrade head` -> `pytest`
- **Acceptance:**
  - E2E green against a real Postgres test DB
  - Coverage >= 80% on `services/` and `api/routers/`
  - `README.md` covers setup, migrations, seeding, running tests
  - CI runs migrations + tests on push and on every PR into `main`
  - `docker compose -f docker-compose.test.yml up -d --wait` then `pytest` works identically on a dev machine and in CI
  - Test DB teardown leaves no volume behind; running the suite does not touch dev data

---

## Execution order

1. **M0** - app boots and tests run; nothing else is verifiable without it
2. **M1** - schema exists; check by hand in psql before writing endpoints
3. **M2** - auth unblocks every protected route; exercise via `/docs` immediately
4. **M3** - gives real data to order against; seed manually here
5. **M4** - orders need menu + auth; first demoable slice after this
6. **M5** - transitions need orders to exist
7. **M6** - admin views need orders in varied states
8. **M7** - polish once behaviour is locked

Each milestone ends green: `alembic upgrade head && pytest` passes before the next starts.

## Branching

`main` is **protected - no direct commits**. Every change lands through a PR from one of the branches below.

| Branch | Carries | Merges after |
| --- | --- | --- |
| `plan/initial` | `DECISIONS.md`, `PLAN.md`, `PROMPTS.md` | plan review |
| `feat/00-scaffolding` | M0 | M0 acceptance |
| `feat/01-models-migration` | M1 | M1 acceptance |
| `feat/02-auth` | M2 | M2 acceptance |
| `feat/03-restaurants` | M3 | M3 acceptance |
| `feat/04-orders` | M4 | M4 acceptance |
| `feat/05-transitions` | M5 | M5 acceptance |
| `feat/06-admin` | M6 | M6 acceptance |
| `feat/07-hardening` | M7 | M7 acceptance |

- One branch per milestone, branched from `main` after the previous one merges - they are sequential, not parallel, because each milestone builds on the last
- A branch merges only when its own acceptance criteria pass; the PR description lists them as a checklist
- Enforcement on `main`: require a PR, require the CI check green, block force-push and deletion. Until CI exists (M7), the check requirement is satisfied by running `alembic upgrade head && pytest` locally and recording the result in the PR.
- Protection applies from `plan/initial` onward - the first commit on `main` should be the initial-commit/README skeleton, before the rule is turned on, so there is a base to branch from

## Cross-cutting conventions

- Money is `Numeric(10,2)` / `Decimal` everywhere, never float
- All timestamps `timezone=True`, stored UTC
- Routers stay thin; business rules live in `services/`
- One Alembic revision per milestone that touches the schema
- JWT: HS256 only, algorithm passed as an explicit allowlist on every `decode`; `sub` is the user id (stringified), never the email
- Auth failures return 401 with `WWW-Authenticate: Bearer` and a generic detail; authorization failures return 403
- No refresh tokens, no revocation list, no password reset in this scope - access tokens expire and the client logs in again
- `tests/conftest.py` fixtures: `db_session` (per-test transaction rollback), `client`, `user_token` / `admin_token` (minted directly via `create_access_token`), `expired_token`
