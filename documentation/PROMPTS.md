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

- **`GET /restaurants` returns only active restaurants** - browsing implies open for
  business. Enforced by `where(Restaurant.is_active.is_(True))` on the listing query and
  covered by `test_inactive_restaurant_not_in_public_listing`.
  - Decided at M3 but shipped unimplemented - the listing had no filter and no test. Caught
    while updating the README on `feat/06-admin` and corrected there.
  - The filter is on the listing alone. `GET /restaurants/{id}` and `/{id}/items` still
    answer for an inactive restaurant: `_get_restaurant` is shared with the admin write
    paths, which need every row. Ordering is refused either way - `create_order` rejects an
    inactive restaurant with 400.
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
  - M3 split the two databases on paper only. `.env.example` named `hijack_takeaway` and
    said nothing about a second one, while the local `.env` pointed `DATABASE_URL` at
    `hijack_takeaway_dev` - so seeding and pytest shared one database, and the M3 entry
    above claims a pattern `.env.example` did not actually carry.
  - Verified around it at the time rather than through it: M7 owns the throwaway test
    database, and editing conftest or deleting the seeded row were both out of bounds, so
    M5 was run against a scratch database created and dropped for the run - 135 passed,
    dev data and every committed file untouched.
  - **Since fixed** - `.env` now defaults to `hijack_takeaway` with the dev database as an
    explicit override, and `.env.example` documents both, which command belongs to which,
    and why the pair must not be crossed. The suite runs clean on the default config.
    The seeding one-liner is recorded with its `SessionLocal` import: the form first
    written down omitted it and raises `NameError`.

## M6 - Admin endpoints

Reported before implementing, per the standing rules:

- **`PaginatedResponse[T]` went to a new `app/schemas/pagination.py`** - the plan names the
  schema as an M6 key item but lists no file for it, and the target layout puts pydantic
  models in `schemas/`. `app/schemas/user.py` likewise gained `UserAdminUpdate` for the
  `PATCH /admin/users/{id}` body, the same way M5's `OrderStatusUpdate` went to
  `schemas/order.py`.
- **`GET /orders` still returns `list[OrderOut]`** - M4 deferred the wrapper to M6, but
  M6's file list does not include the orders router, and "shared" is satisfied by the three
  admin listings. Retrofitting it would change a response shape already accepted at M4.
- **Date range is `created_from` / `created_to`, both inclusive on `Order.created_at`** -
  the plan names the filter, not the parameters.
- **A bound sent without an offset is read as UTC** - `created_at` is `timestamptz` and all
  stored values are UTC, but psycopg hands a naive datetime to Postgres as a plain
  timestamp, which resolves against the session's `TimeZone`. The same query would then
  select different orders on two differently configured servers.
- **`created_from` later than `created_to` is 422, not an empty page** - such a range can
  never match, and answering "no orders" hides the fact that the range itself is wrong.
- **Paging bounds copied from `GET /orders`** - `limit` 1-100 default 20, `offset` >= 0, so
  one rule covers every listing in the API.
- **Newest first is `created_at DESC, id DESC`** - M4's tiebreaker, for M4's reason:
  Postgres `now()` is the transaction clock, so orders placed together share a timestamp.
- **`GET /admin/users` is ordered by id, not newest first** - `UserOut` carries no
  timestamp, so a descending list would be sorted by something the caller cannot see.
- **403 comes from `admin: AdminUser` on every signature** - not from the router's own
  `dependencies=[...]`, which needs the inline dependency call M2 ruled out and its
  convention test greps for. That grep reads comments too, so even explaining the
  alternative in a comment breaks the suite - found the hard way.
- **Filters are not lookups** - an unknown `restaurant_id` or `customer_id` on
  `/admin/orders` yields an empty page, which is also the honest answer for a real id with
  no orders. `/admin/restaurants/{id}/orders` does look the restaurant up, so an unknown id
  there is a 404: asking a named restaurant for its orders and being handed silence hides a
  mistake. An unknown user on `PATCH /admin/users/{id}` is a 404 as well, per M3's
  convention.
- **No filters beyond `limit`/`offset` on `GET /admin/users` and
  `/admin/restaurants/{id}/orders`** - the plan names filters for `/admin/orders` only.
- **No last-admin guard on `PATCH /admin/users/{id}`** - an admin can demote or deactivate
  themselves, locking the role out of a running system. A real footgun, reported as such,
  and left in because the plan names no such rule; a test documents the behaviour so it
  reads as decided rather than missed.
- **The route also refuses to edit anything else** - `email`, `full_name` and
  `hashed_password` in the body are ignored, not applied. Promoting an account must not
  double as a way to take it over.

Found while building it:

- **Caught: an explicit `null` reached the database.** `{"role": null}` sets the field
  rather than leaving it unset, so `model_dump(exclude_unset=True)` carried it into an
  UPDATE that Postgres refused - a 500 for plainly bad input. `None` is how absence is
  spelled in these PATCH bodies, so it cannot also be a value: a `field_validator` on
  `UserAdminUpdate` now rejects it with 422. Only an explicitly sent null reaches the
  validator, because pydantic does not validate defaults.
  - **The same flaw is still open in M3** - `RestaurantUpdate` and `ItemUpdate` accept
    `null` for `name`, `address`, `is_active`, `price` and `is_available`, all NOT NULL
    columns, so `PATCH /restaurants/{id}` with `{"name": null}` returns 500. Probed with a
    throwaway probe, then left alone: M3's schemas are outside M6's file list. Recorded in
    the README as a known bug.
- **Caught: a range with one bound offsetless raised `TypeError`.** Comparing a naive
  datetime with an aware one is a 500, and both forms are legal input. The two bounds are
  now put on the same clock *before* they are compared, with tests for either ordering.
- **The count and the page come from one statement** - `_page` counts
  `stmt.order_by(None).subquery()` and then slices the same `stmt`, so a filter added to a
  listing is counted by construction. A second hand-written count query is the kind that
  silently drifts from the one it is meant to mirror.
- **The 403 tests are parametrized over routes discovered from the router** - read off
  `admin.router.routes` rather than listed by hand, so a route added later is covered the
  moment it exists. A separate test asserts the discovered list is exactly the four
  endpoints, so the parametrization cannot quietly go empty.
- **Order rows in `tests/test_admin.py` are written straight to the table** - not placed
  through `POST /orders`, not walked with `PATCH /orders/{id}/status`. Those paths belong
  to M4 and M5, and neither lets a test choose `created_at` or reach `delivered` in one
  step; the date-range filter is untestable without that control, since every
  default-stamped row inside one transaction shares a timestamp. One test does place an
  order over HTTP and assert it appears in every admin view.
