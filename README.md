# Takeaway Platform Backend

Minimal FastAPI backend for a takeaway platform: customers browse restaurants & menus, place orders, and track order status. Admins manage restaurants, menu items and order status transitions, read the whole order book, and manage accounts.

## Status

All milestones are merged or ready to merge — 280 tests passing.

- [x] M0: Scaffolding
- [x] M1: Models + migrations
- [x] M2: Auth
- [x] M3: Restaurants & menu
- [x] M4: Order placement
- [x] M5: Status transitions
- [x] M6: Admin endpoints
- [x] M7: Hardening + CI
- [x] M8: Deployment — Docker, nginx, lint/type gates

**Delivered ahead of its milestone:** [`app/db/seed.py`](app/db/seed.py) (an M7 item) landed with M3, and [`app/core/errors.py`](app/core/errors.py) landed with M2 as a two-class stub because `decode_token` needed `CredentialsError`. M7 completed that hierarchy and registered the handlers.

**Fixed in M7** — M3's `RestaurantUpdate` and `ItemUpdate` used to accept an explicit `null` for columns that are NOT NULL, so `PATCH /restaurants/{id}` with `{"name": null}` (likewise `address`, `is_active`, and `PATCH /items/{id}`'s `name`, `price`, `is_available`) reached the database and returned 500 instead of 422. `None` is how those schemas spell "field absent", so it cannot also be a value. Found while building M6, where [`UserAdminUpdate`](app/schemas/user.py) already rejected the same input with 422, and left alone then as outside M6's scope. Both schemas now carry the same `reject_an_explicit_null` validator — see [null in a PATCH body](#null-in-a-patch-body).

## Tech Stack

- **Framework:** FastAPI 0.141
- **Database:** PostgreSQL 17
- **ORM:** SQLAlchemy 2.0
- **Migrations:** Alembic
- **Validation:** Pydantic v2 + pydantic-settings
- **Auth:** JWT (PyJWT, HS256) + pwdlib/Argon2
- **Python:** 3.12+
- **Serving:** Gunicorn + `uvicorn_worker.UvicornWorker`, behind nginx
- **Containers:** Docker multi-stage build, docker-compose

Every dependency is `==`-pinned in [pyproject.toml](pyproject.toml), with a resolved [requirements.lock](requirements.lock) alongside.

## Layout

```
app/
  main.py                  # app factory, router wiring, GET /health
  core/config.py           # Settings (pydantic-settings), cached get_settings()
  core/security.py         # pwdlib hashing + pyjwt encode/decode
  core/errors.py           # AppError hierarchy + exception handlers
  core/logging.py          # JSON formatter, redaction filter, request logging
  db/base.py               # DeclarativeBase + model imports for Alembic
  db/session.py            # engine, SessionLocal, get_db
  db/seed.py               # dev seed: admin + 2 restaurants with menus
  models/                  # user, restaurant, restaurant_item, order, order_item, enums
  schemas/                 # pydantic in/out per resource, plus PaginatedResponse[T]
  api/deps.py              # CurrentUser / AdminUser / OptionalUser aliases
  api/routers/             # auth, restaurants, orders, admin
  services/                # order_service, transitions
alembic/versions/          # 0001_initial.py — all 5 tables + enum types
tests/                     # 280 tests
```

## Run with Docker

The whole stack — Postgres, the API under Gunicorn, and nginx in front of it — from one command
and a clean clone. No virtualenv, no `.env`, no separate migration step.

```bash
docker compose up --build
```

Then:

```bash
curl -i localhost:8080/health     # {"status": "ok"}
```

Interactive docs at http://localhost:8080/docs.

| | |
| --- | --- |
| Public endpoint | **http://localhost:8080** — nginx, the only published application port |
| API container | `api:8000` on the compose network — **not** published to the host |
| Database | `localhost:5432`, as before |

`localhost:8000` is deliberately refused from the host. nginx being the sole ingress is what makes
Gunicorn's `--forwarded-allow-ips='*'` safe: the API trusts `X-Forwarded-For` because nginx is the
only client that can reach it and sets that header itself. Publish the API directly and that flag
has to go at the same time.

**Migrations run themselves.** [docker/entrypoint.sh](docker/entrypoint.sh) runs
`alembic upgrade head` before starting Gunicorn, so a first boot against an empty volume builds
the schema and a later boot is a no-op. `docker compose up` blocks until the API passes its
healthcheck, so the first request after it returns will succeed rather than hitting a proxy whose
upstream is still migrating.

### Configuration

`JWT_SECRET` defaults to `dev-only-not-for-deployment` so a fresh clone boots with no `.env` at
all. **That default is for local development and nothing else.** Override it anywhere real:

```bash
JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(64))") docker compose up --build
```

Tokens are signed with this value, so anyone holding it can mint an admin token. `DATABASE_URL` is
set by compose to reach the `postgres` service by name and needs no override.

### Useful commands

```bash
docker compose up --build -d          # background
docker compose logs -f api            # migration output, then Gunicorn's access log
docker compose ps                     # health status per service
docker compose exec api alembic current
docker compose down                   # stop; keeps the database volume
docker compose down -v                # stop and delete the volume, so the next boot migrates from scratch
```

To run only the database — the setup the local workflow below expects — start that one service:

```bash
docker compose up -d postgres
```

## Getting Started

The local path, for development against `--reload`. [Run with Docker](#run-with-docker) is the
faster way to simply get the API running.

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

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | none — required | fails `alembic`, `uvicorn` and `pytest` alike if unset |
| `JWT_SECRET` | none — required | generate per environment; the app refuses to start without it |
| `JWT_ALGORITHM` | `HS256` | decoding is pinned to an `HS256` allowlist regardless |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | |
| `CORS_ORIGINS` | `["*"]` | JSON list; narrow it per deployment, e.g. `CORS_ORIGINS=["https://app.example.com"]` |

### 3. Start Postgres

```bash
docker compose up -d postgres
```

This creates the `hijack_takeaway` database on port 5432 — the one the default `DATABASE_URL` and the test suite both use.

Name the service explicitly: since M8, a bare `docker compose up -d` starts the API and nginx as well, which is [the Docker path](#run-with-docker) rather than this one.

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
docker compose -f docker-compose.test.yml up -d --wait
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55432/hijack_takeaway_test alembic upgrade head
pytest
docker compose -f docker-compose.test.yml down -v    # cleanup; leaves no volume
```

280 tests, ~17s. Each test runs inside a transaction that is rolled back afterwards, so the suite leaves no rows behind.

`pytest` needs no environment of its own: [tests/conftest.py](tests/conftest.py) injects the throwaway database URL unless `DATABASE_URL` already names one, and an injected environment variable outranks `.env`. Only `alembic` needs the URL spelled out, since it reads the app's own settings.

[docker-compose.test.yml](docker-compose.test.yml) has its own compose project, service (`postgres-test`), database and host port (55432), and keeps its data directory on `tmpfs` — so it cannot reach the dev database on 5432, and there is no volume to remove afterwards. `--wait` blocks on the healthcheck, so `alembic` starts against a server that is already accepting connections.

### The suite will not run against a non-test database

`assert_is_a_test_database` in [tests/conftest.py](tests/conftest.py) allows any database whose name ends in `_test`, plus M0's `hijack_takeaway`. Anything else — `hijack_takeaway_dev`, a staging URL, a production one — aborts collection before a single connection is opened:

```
RuntimeError: Refusing to run the test suite against database 'hijack_takeaway_dev':
it is not a test database.
```

That is what makes "running the suite does not touch dev data" a property of the code rather than a convention in this file. `DATABASE_URL=$DEV_DB pytest` — the mistake that cost M5 an afternoon of duplicate-key errors — now fails loudly instead of writing to seeded data.

To run against the database from `docker compose up` instead, name it explicitly:

```bash
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/hijack_takeaway pytest
```

### Coverage

```bash
pytest --cov=app/services --cov=app/api/routers --cov-report=term-missing
```

99% on `app/services/` + `app/api/routers/`, against M7's ≥80% bar. The floor is enforced in CI with `--cov-fail-under=80` rather than in `pyproject.toml`, so a plain local `pytest` stays fast and collects no coverage.

### CI

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs on every push to `main` and every PR into it: install from [requirements.lock](requirements.lock) → start the throwaway Postgres → `alembic upgrade head` → `pytest` with the coverage gate → tear the database down. The teardown is `if: always()`, so a red run cleans up too.

## Seeding (dev database only)

`seed()` writes `admin@example.com`, the exact address conftest's `admin` fixture inserts, so a seeded database and the test suite cannot share a home — seeding `hijack_takeaway` makes the suite fail with duplicate-key errors on `ix_users_email`. Since M7 the suite defaults to the throwaway database and [refuses a non-test one outright](#the-suite-will-not-run-against-a-non-test-database), so this is now enforced rather than advised. Seed a separate database and point the server at it explicitly:

```bash
# One-time: docker-compose only creates hijack_takeaway, so create the dev DB yourself
docker compose exec postgres createdb -U postgres hijack_takeaway_dev

export DEV_DB=postgresql+psycopg://postgres:postgres@localhost:5432/hijack_takeaway_dev
DATABASE_URL=$DEV_DB alembic upgrade head
DATABASE_URL=$DEV_DB python -c "from app.db.session import SessionLocal; from app.db.seed import seed; seed(SessionLocal())"
DATABASE_URL=$DEV_DB uvicorn app.main:app --reload
```

Seeding is idempotent by name and email, so re-running it after a migration adds nothing. It creates `admin@example.com` / `admin123` (a dev placeholder, never a credential), Pizza Place (Margherita 12.99, Pepperoni 14.99) and Burger Joint (Cheeseburger 9.99, Fries 3.99).

Since M7 there is a simpler option: run the suite against the throwaway database from [docker-compose.test.yml](docker-compose.test.yml) (see [Tests](#tests)), and the split stops mattering — `hijack_takeaway` can be seeded freely once `pytest` no longer runs there.

## API

`admin` implies a valid token whose role is `admin`; `auth` implies any valid token.

| Method | Path | Access | Notes |
| --- | --- | --- | --- |
| GET | `/health` | public | `{"status": "ok"}` |
| POST | `/auth/register` | public | 201; always role `user`; duplicate email → 409 |
| POST | `/auth/login` | public | form-encoded; `username` is the email |
| GET | `/auth/me` | auth | |
| GET | `/restaurants` | public | active restaurants only |
| GET | `/restaurants/{id}` | public | unknown id → 404; answers for inactive restaurants too |
| GET | `/restaurants/{id}/items` | public | available items only; `?include_unavailable=true` needs admin (401 anonymous, 403 non-admin) |
| POST | `/restaurants` | admin | 201 |
| PATCH | `/restaurants/{id}` | admin | applies only the fields sent; an explicit `null` for a NOT NULL field → 422 |
| POST | `/restaurants/{id}/items` | admin | 201 |
| PATCH | `/items/{id}` | admin | also toggles `is_available`; same `null` rule |
| POST | `/orders` | auth | 201; validates the basket against the live menu, snapshots prices |
| GET | `/orders` | auth | own orders only; `limit` (1–100, default 20) / `offset`; newest first |
| GET | `/orders/{id}` | auth | owner or admin; someone else's order reads as 404, not 403 |
| PATCH | `/orders/{id}/status` | admin | body `{"status": ...}`; illegal move → 409 |
| GET | `/admin/orders` | admin | every order, newest first; filters below |
| GET | `/admin/restaurants/{id}/orders` | admin | one restaurant's order book; unknown id → 404 |
| GET | `/admin/users` | admin | every account, by id; `UserOut`, so no password field |
| PATCH | `/admin/users/{id}` | admin | body `{"role": ..., "is_active": ...}`, both optional |

Order status moves one step at a time: `pending → accepted → out_for_delivery → delivered`. `delivered` is terminal; there is no cancellation in this scope.

`order_items.unit_price` is snapshotted at order time, so re-pricing a dish never restates what a customer was charged.

### `null` in a PATCH body

Every PATCH body here is a schema whose fields all default to `None`, and the route applies `model_dump(exclude_unset=True)` — so `None` is how these schemas spell "field absent", and a field left out of the body is left alone in the row.

That means `None` cannot also be a value. For a column that is NOT NULL, sending an explicit `null` would otherwise mark the field as set and carry the null down to an UPDATE the database refuses, turning bad input into a 500. A `reject_an_explicit_null` validator on [`RestaurantUpdate`](app/schemas/restaurant.py), [`ItemUpdate`](app/schemas/restaurant_item.py) and [`UserAdminUpdate`](app/schemas/user.py) answers 422 instead:

| Field | Sending `null` |
| --- | --- |
| `name`, `address`, `is_active` on a restaurant | 422 |
| `name`, `price`, `is_available` on an item | 422 |
| `role`, `is_active` on a user | 422 |
| `phone` on a restaurant | 200 — clears the number |
| `description` on an item | 200 — clears the text |

The two nullable columns are deliberately outside the validator: there, a null is a real value, and clearing a field has to stay possible. Only an explicitly sent null reaches the validator — Pydantic does not validate defaults, so an omitted field keeps its `None` and stays excluded.

### Admin listings

All three `/admin` listings answer with the same envelope, where `total` counts every row the filters matched rather than the rows returned:

```json
{ "items": [], "total": 0, "limit": 20, "offset": 0 }
```

`limit` is 1–100 (default 20) and `offset` ≥ 0 on every listing, the same bounds `GET /orders` uses. `GET /admin/orders` takes five further filters, each optional and all ANDed together:

| Filter | Accepts | Notes |
| --- | --- | --- |
| `status` | an `OrderStatus` value | anything else → 422 |
| `restaurant_id` | integer | a filter, not a lookup: an id matching nothing is an empty page, not a 404 |
| `customer_id` | integer | same |
| `created_from` | ISO 8601 datetime | inclusive; a value with no offset is read as UTC |
| `created_to` | ISO 8601 datetime | inclusive; `created_from` later than `created_to` → 422 |

`GET /admin/restaurants/{id}/orders` is the same page narrowed to one restaurant, with the restaurant looked up first so an unknown id is a 404 instead of silence. An inactive restaurant still has an order book.

`PATCH /admin/users/{id}` edits the role and the activation flag and nothing else — an `email`, `full_name` or `hashed_password` in the body is ignored. An omitted field is left alone; an explicit `null` is 422, because `null` is how absence is spelled. Deactivation takes effect immediately, including on tokens already issued: `get_current_user` re-reads `is_active`, so no blacklist is involved. Nothing prevents an admin from demoting or deactivating themselves — the plan names no last-admin guard, so none was invented.

## Errors and logging

Every expected failure answers in one shape:

```json
{ "detail": "Cannot move an order from pending to delivered" }
```

Services raise from the `AppError` hierarchy in [app/core/errors.py](app/core/errors.py) — `NotFoundError` (404), `BusinessRuleError` (400), `ConflictError` (409), `CredentialsError` (401, with `WWW-Authenticate: Bearer`) — and a handler registered in the app factory renders each one, which is what keeps `fastapi` out of the service layer. Routers still raise FastAPI's `HTTPException` for plumbing (a missing row, a forbidden role); it produces the same body.

Two deliberate exceptions to the single shape:

- **422** keeps FastAPI's list of per-field errors under `detail` — more useful than one sentence, and the API's answer since M0.
- **500** is always `{"detail": "Internal server error"}`. The traceback goes to the log and never to the response: a stack trace in a body hands over the file layout, the library versions and often the SQL.

Logs are JSON lines, one object per record, with a line per request carrying `method`, `path`, `status_code` and `duration_ms` — metadata only. Bodies are never read into a record, and the path is logged without its query string. A redaction filter on the handler blanks anything filed under `Authorization`, `Cookie`, `Set-Cookie`, `password`, `token`, `secret` and their neighbours, then scrubs credential-shaped text (`Bearer …`, `password=…`) out of message strings as a second line of defence.

## Security notes

- **JWT secret** — no default; the app refuses to start without `JWT_SECRET`. Generate one per environment (`python -c "import secrets; print(secrets.token_urlsafe(64))"`) and keep it out of git — `.env` is gitignored.
- **Tokens** — HS256 only, with the algorithm passed as an explicit allowlist on every decode, so a token declaring `alg: none` or `HS512` is rejected whatever `JWT_ALGORITHM` says. `sub` is the user id, never the email. Access tokens expire after `ACCESS_TOKEN_EXPIRE_MINUTES` (default 30); there are no refresh tokens and no revocation list by design, and a deactivated account is refused on its next request because `get_current_user` re-reads `is_active`.
- **Passwords** — Argon2 via pwdlib, one way. No schema or endpoint ever returns the hash.
- **CORS** — `CORS_ORIGINS` defaults to `["*"]` with credentials disabled. The wildcard is defensible only because this API authenticates with a Bearer header the client attaches deliberately, never a cookie the browser would send on its own; narrow it to your real origins in production regardless.
- **Logs** — no bodies, no headers, and a redaction filter over everything that is written. See above.
- **Failure codes** — authentication failures answer 401 with a generic detail and `WWW-Authenticate: Bearer`; authorization failures answer 403. An unknown email and a wrong password are indistinguishable, so login cannot be used to enumerate accounts.

## Documentation

- [DECISIONS.md](documentation/DECISIONS.md) — data model, relations, auth design, out-of-scope features
- [PLAN.md](documentation/PLAN.md) — milestones, acceptance criteria, execution order, branching
- [PLAN_08_DEPLOYMENT_AND_HARDENING.md](documentation/PLAN_08_DEPLOYMENT_AND_HARDENING.md) — the M8 amendment: Docker, nginx, lint/type gates

---

_Protected branch: `main`. All changes via PR, one branch per milestone._
