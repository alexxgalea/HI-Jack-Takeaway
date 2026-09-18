# Takeaway Platform Backend

Minimal FastAPI backend for a takeaway platform: customers browse restaurants & menus, place orders, and track order status. Admins manage restaurants, menu items, and order status transitions.

## Status

Milestones M0–M5 are merged into `main` and green (135 tests passing).

- [x] M0: Scaffolding
- [x] M1: Models + migrations
- [x] M2: Auth
- [x] M3: Restaurants & menu
- [x] M4: Order placement
- [x] M5: Status transitions
- [ ] M6: Admin endpoints — not started
- [ ] M7: Hardening + CI — partially delivered (see below)

**Delivered ahead of its milestone:** [`app/db/seed.py`](app/db/seed.py) (an M7 item) landed with M3, and [`app/core/errors.py`](app/core/errors.py) landed with M2 as a two-class stub because `decode_token` needed `CredentialsError`.

**Not implemented yet** — described in [PLAN.md](documentation/PLAN.md), absent from the code:

- `app/api/routers/admin.py` and every `/admin/*` route (`GET /admin/orders`, `GET /admin/users`, `PATCH /admin/users/{id}`, `GET /admin/restaurants/{id}/orders`)
- The shared `PaginatedResponse[T]` schema — `GET /orders` returns a plain `list[OrderOut]`
- Exception handlers for the `AppError` hierarchy; nothing is registered on the app, so error shapes are FastAPI's defaults
- Structured logging with redaction, and CORS middleware
- `tests/test_e2e.py`, `docker-compose.test.yml`, `.github/workflows/ci.yml` — there is no CI; the suite runs locally only

## Tech Stack

- **Framework:** FastAPI 0.141
- **Database:** PostgreSQL 17
- **ORM:** SQLAlchemy 2.0
- **Migrations:** Alembic
- **Validation:** Pydantic v2 + pydantic-settings
- **Auth:** JWT (PyJWT, HS256) + pwdlib/Argon2
- **Python:** 3.12+

Every dependency is `==`-pinned in [pyproject.toml](pyproject.toml), with a resolved [requirements.lock](requirements.lock) alongside.

## Layout

```
app/
  main.py                  # app factory, router wiring, GET /health
  core/config.py           # Settings (pydantic-settings), cached get_settings()
  core/security.py         # pwdlib hashing + pyjwt encode/decode
  core/errors.py           # AppError, CredentialsError (stub; no handlers yet)
  db/base.py               # DeclarativeBase + model imports for Alembic
  db/session.py            # engine, SessionLocal, get_db
  db/seed.py               # dev seed: admin + 2 restaurants with menus
  models/                  # user, restaurant, restaurant_item, order, order_item, enums
  schemas/                 # pydantic in/out per resource
  api/deps.py              # CurrentUser / AdminUser / OptionalUser aliases
  api/routers/             # auth, restaurants, orders
  services/                # order_service, transitions
alembic/versions/          # 0001_initial.py — all 5 tables + enum types
tests/                     # 135 tests
```

## Getting Started

### 1. Install

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```

### 2. Configure

The app reads its settings from `.env` and **refuses to start without them** — `DATABASE_URL` and `JWT_SECRET` have no defaults, and the engine is built at import time, so a missing value fails `alembic`, `uvicorn` and `pytest` alike.

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"   # paste into JWT_SECRET
```

### 3. Start Postgres

```bash
docker compose up -d
```

This creates the `hijack_takeaway` database on port 5432 — the one the default `DATABASE_URL` and the test suite both use.

### 4. Run migrations

```bash
alembic upgrade head
```

`alembic.ini` carries no `sqlalchemy.url`; [alembic/env.py](alembic/env.py) takes the connection string from `Settings`, so migrations always target the same database as the app.

### 5. Run the server

```bash
uvicorn app.main:app --reload
```

Interactive docs at http://127.0.0.1:8000/docs — the Authorize button drives the real `POST /auth/login` password flow.

## Tests

```bash
pytest
```

135 tests, ~8s. Each test runs inside a transaction that is rolled back afterwards, so the suite leaves no rows behind.

Coverage on the layers M7 sets a floor for:

```bash
pytest --cov=app/services --cov=app/api/routers --cov-report=term
```

Currently 99% (`app/services/` + `app/api/routers/`), against M7's ≥80% bar.

## Seeding (dev database only)

**Do not seed the database pytest uses.** `seed()` writes `admin@example.com`, which is the exact address conftest's `admin` fixture inserts — seeding `hijack_takeaway` makes the suite fail with duplicate-key errors on `ix_users_email`. Seed a separate database and point the server at it explicitly:

```bash
# One-time: docker-compose only creates hijack_takeaway, so create the dev DB yourself
docker compose exec postgres createdb -U postgres hijack_takeaway_dev

export DEV_DB=postgresql+psycopg://postgres:postgres@localhost:5432/hijack_takeaway_dev
DATABASE_URL=$DEV_DB alembic upgrade head
DATABASE_URL=$DEV_DB python -c "from app.db.session import SessionLocal; from app.db.seed import seed; seed(SessionLocal())"
DATABASE_URL=$DEV_DB uvicorn app.main:app --reload
```

Seeding is idempotent by name and email, so re-running it after a migration adds nothing. It creates `admin@example.com` / `admin123` (a dev placeholder, never a credential), Pizza Place (Margherita 12.99, Pepperoni 14.99) and Burger Joint (Cheeseburger 9.99, Fries 3.99).

M7 replaces this split with a throwaway `docker-compose.test.yml`.

## API

`admin` implies a valid token whose role is `admin`; `auth` implies any valid token.

| Method | Path | Access | Notes |
| --- | --- | --- | --- |
| GET | `/health` | public | `{"status": "ok"}` |
| POST | `/auth/register` | public | 201; always role `user`; duplicate email → 409 |
| POST | `/auth/login` | public | form-encoded; `username` is the email |
| GET | `/auth/me` | auth | |
| GET | `/restaurants` | public | returns **all** restaurants, active or not |
| GET | `/restaurants/{id}` | public | unknown id → 404 |
| GET | `/restaurants/{id}/items` | public | available items only; `?include_unavailable=true` needs admin (401 anonymous, 403 non-admin) |
| POST | `/restaurants` | admin | 201 |
| PATCH | `/restaurants/{id}` | admin | applies only the fields sent |
| POST | `/restaurants/{id}/items` | admin | 201 |
| PATCH | `/items/{id}` | admin | also toggles `is_available` |
| POST | `/orders` | auth | 201; validates the basket against the live menu, snapshots prices |
| GET | `/orders` | auth | own orders only; `limit` (1–100, default 20) / `offset`; newest first |
| GET | `/orders/{id}` | auth | owner or admin; someone else's order reads as 404, not 403 |
| PATCH | `/orders/{id}/status` | admin | body `{"status": ...}`; illegal move → 409 |

Order status moves one step at a time: `pending → accepted → out_for_delivery → delivered`. `delivered` is terminal; there is no cancellation in this scope.

`order_items.unit_price` is snapshotted at order time, so re-pricing a dish never restates what a customer was charged.

## Documentation

- [DECISIONS.md](documentation/DECISIONS.md) — data model, relations, auth design, out-of-scope features
- [PLAN.md](documentation/PLAN.md) — milestones, acceptance criteria, execution order, branching

---

_Protected branch: `main`. All changes via PR, one branch per milestone._
