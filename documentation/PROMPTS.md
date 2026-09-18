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
