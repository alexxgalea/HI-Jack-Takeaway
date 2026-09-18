# Prompt log

What was asked of the assistant, recorded as decisions rather than transcripts.
[PLAN.md](PLAN.md) holds the spec and [DECISIONS.md](DECISIONS.md) the architecture
that outlives this project; this file is the trail of *why the plan changed when it
did*, in the order it happened.

One section per milestone. One bullet per decision, short enough to scan - extend any
of them with nested bullets when a decision needs its reasoning kept.

## Standing rules

Carried by every build prompt, so they are stated once here instead of repeated per entry:

- Implement strictly what PLAN.md specifies for the milestone.
- Report any departure from it *before* writing code - no out-of-bound decisions.
- Stop after showing diffs; commits and PRs are made by hand.

## Planning

- **Plan shape** - milestones, each with files, key functions and classes, and acceptance
  criteria, ordered so the app can be run and tested incrementally. Bullets, no prose.
- **Auth locked to custom JWT (PyJWT), not fastapi-users** - default FastAPI approach,
  minimal dependencies, full control over token payload, expiry and role checks.
  Fixes `OAuth2PasswordBearer(tokenUrl="/auth/login")`, `algorithms=["HS256"]`, pwdlib
  for hashing, `get_current_user` / `require_admin` dependencies, 15-30 min tokens.
- **DECISIONS.md restated to match** - the auth decision is architecture, not a milestone
  detail, so it lives there too.
- **Versions frozen** - every dependency `==`-pinned in `pyproject.toml` with a lockfile.
- **Async test wiring from the start** - `pytest-asyncio` and `httpx` in the test deps.
- **`CurrentUser` / `AdminUser` as `Annotated` type aliases** - routers never spell out
  `Depends(...)` inline.
- **`python-multipart` is mandatory, not optional** - `OAuth2PasswordRequestForm` parses
  multipart; easy to drop when trimming deps because no application code imports it.
- **Throwaway test Postgres deferred to M7** - `docker-compose.test.yml`, never the dev DB.
- **One branch per milestone** - `plan/initial` plus `feat/00..07`, sequential; `main`
  protected against direct commits.

## M0 - Scaffolding

- **Reviewed against the acceptance criteria before moving on** - every scaffolding file
  checked for spec match, hardcoded secrets, and anti-patterns.
- **Caught: `JWT_SECRET` did not fail loudly when empty** - a required `str` field rejects
  a *missing* secret but accepts a blank one, so startup succeeded with no signing key.
  Fixed with a `field_validator` on `Settings.jwt_secret`.

## M1 - Models + first migration

- Built to plan; no departures.

## M2 - Auth (custom JWT, pyjwt + pwdlib)

- Built to plan; no departures.

## M3 - Restaurants & menu

- **`GET /restaurants` lists active restaurants only** - browsing implies active ones.
  Covered by a test asserting an inactive restaurant is absent from the public listing.
- **Seed script pulled forward from M7** - `app/db/seed.py` creates the admin, Pizza Place
  (Margherita 12.99, Pepperoni 14.99) and Burger Joint (Cheeseburger 9.99, Fries 3.99),
  all active and available. Called out in the PR description as an early M7 file.
- **Dev and test databases split** - seeding targets `hijack_takeaway_dev`; pytest keeps
  `hijack_takeaway` untouched. The pattern is documented in `.env.example` until M7
  replaces it with `docker-compose.test.yml`.

## M4 - Order placement

Reported before implementing, per the standing rules:

- **Pagination stays a plain list** - `GET /orders` takes `limit`/`offset` and returns
  `list[OrderOut]`. The shared `PaginatedResponse[T]` belongs to M6; wrapping it now would
  pull M6 forward.
- **404 for a missing restaurant, 400 for a rejected basket** - an unknown `restaurant_id`
  follows M3's unknown-id convention; an inactive restaurant, a foreign item, an
  unavailable item and an unknown item id are all business-rule rejections. An unknown item
  id answers identically to a foreign one, so the endpoint cannot probe which ids exist.
- **`quantity > 0` is enforced twice** - `Field(gt=0)` on the schema, so over HTTP the
  status is 422; the check inside `create_order` guards direct service calls.
- **Two additions the plan does not name** - a basket must carry at least one line
  (`min_length=1`), and `GET /orders` sorts `created_at DESC, id DESC`. The id tiebreaker
  is load-bearing: Postgres `now()` is the transaction clock, so orders placed together
  share a timestamp.

## M5 - Status transitions

- **`OrderStatusUpdate` added to `app/schemas/order.py`** - the plan's M5 file list names
  only `transitions.py`, the orders router and the test file, but the `{"status": ...}`
  body needs a model. It went to the resource's schema module, per the target layout.
  Reported on delivery rather than before: the gap only showed while wiring the route.
- **409 for an illegal move, 404 for a missing order** - an illegal move is a conflict with
  the state the order is in, not a malformed request, so `assert_transition` raises 409 and
  names both ends in the detail. The missing-order 404 is a plain one, unlike
  `GET /orders/{id}`'s owner-shaped 404: the caller is already an admin, so there is
  nothing left to hide by pretending the id does not exist.
- **The row lock is taken before the check, not around the write** - `set_order_status`
  opens with `db.refresh(order, with_for_update=True)`, so the status the guard reads is
  the committed one.
  - Locking only the write would let two admins both read `accepted`, both pass the guard,
    and the order would take a step it never legally made.
- **`updated_at` is left to the column's own `onupdate=func.now()`** - not restated in the
  service, so there is a single definition of when that clock moves. Verified against
  emitted SQL rather than assumed: `UPDATE orders SET status=..., updated_at=now()`.
  - Not asserted as a strict increase in tests: Postgres `now()` is the transaction clock,
    and the `db_session` fixture runs each test inside one transaction, so an insert and a
    later update share a timestamp there. Same root cause as M4's id tiebreaker.
- **`can_transition` reads the table with `.get(current, set())`** - a status added to
  `OrderStatus` without a matching table entry then degrades to a 409 instead of a
  `KeyError` surfacing as a 500.
- **Four tests the plan does not name** - a status outside the enum is 422, an empty body
  is 422, an unknown order is 404, and a refused move is asserted to leave the row
  untouched. The matrix test carries that last check on every illegal pair.
- **Caught: the suite was running against the seeded dev database** - 19 errors on `main`
  before a line of M5 was written, all `duplicate key value violates unique constraint
  "ix_users_email"` from conftest's `admin` fixture colliding with the `admin@example.com`
  row `seed()` had already written.
  - M3 split the two databases on paper - `.env.example` points pytest at
    `hijack_takeaway` - but the local `.env` sets `DATABASE_URL` to
    `hijack_takeaway_dev`, so seeding and pytest shared one database after all.
  - Not fixed here: M7 owns the throwaway test database, and editing conftest or deleting
    the seeded row would both be out of bounds. M5 was verified instead by pointing
    `DATABASE_URL` at a scratch database created and dropped for the run - 135 passed,
    with dev data and every committed file untouched.
