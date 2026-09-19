## Key design decisions

- **Model:** `users`, `restaurants`, `restaurant_items`, `orders`, `order_items`.
- **Relations**
  - `restaurants` 1–N `restaurant_items`
  - `users` 1–N `orders`
  - `restaurants` 1–N `orders`
  - `orders` 1–N `order_items`
  - `restaurant_items` 1–N `order_items`
  - Key FKs:
    - `restaurant_items.restaurant_id` → `restaurants.id`
    - `orders.customer_id` → `users.id`
    - `orders.restaurant_id` → `restaurants.id`
    - `order_items.order_id` → `orders.id`
    - `order_items.restaurant_item_id` → `restaurant_items.id`
  - Other constraints: `users.email` unique, indexed.
- **Order status:** enum with fixed values `pending → accepted → out_for_delivery → delivered`; invalid transitions are rejected.
- **Prices:** `order_items.unit_price` is a snapshot at order time, not recomputed from the menu.
- **Availability:** modelled as `restaurant_items.is_available` (bool); no ingredient-level inventory or stock tables.
- **Auth:** Custom JWT with `pyjwt`, **not** `fastapi-users`.
  - *Rationale:* default FastAPI approach, minimal dependencies, full control over token payload, expiration and role checks.
  - Token extraction: `OAuth2PasswordBearer(tokenUrl="/auth/login")`; `POST /auth/login` therefore consumes `OAuth2PasswordRequestForm` (form `username` = email).
  - Tokens: `pyjwt`, HS256, decoded with an explicit `algorithms=["HS256"]` allowlist; claims `sub` (user id), `role`, `exp`, `iat`.
  - Access tokens short-lived (15–30 min). No refresh tokens, no revocation list, no password reset.
  - Passwords: one-way hashing via `pwdlib` (Argon2).
  - Roles: two — `user`, `admin`.
  - Dependencies: `get_current_user` (decode → validate `exp`/`sub` → load user → 401 on any failure), `require_admin` (`user.role == "admin"` else 403).
- **Out of scope:** payments, refunds, coupons, driver assignment, GPS/live tracking, complex inventory.

## M8 - Deployment and tooling

- **Lint and type gates:** `ruff` and `mypy` run in CI as their own job. Neither is named in the
  brief; both are kept because the repo already runs CI the brief never asked for, and a
  config-only gate over existing code carries no product risk.
- **Ruff line length is 100, not Black's 88.** The codebase is hand-formatted in a Black-ish style
  at roughly 90 columns and matches neither width exactly. At 88 the formatter mangles
  `app/main.py`'s settings line into a three-line parenthesised expression; at 100 every change is
  a pure line-join and the author's own lines are left alone.
- **Four ruff rules are ignored, each for a reason, none of them "it was noisy":**
  - `E501` - `ruff format` owns line length and cannot split a long comment or string literal.
  - `UP042` - `(str, Enum)` -> `StrEnum` changes what `str(UserRole.user)` returns, from
    `"UserRole.user"` to `"user"`. That is a behaviour change, not a refactor.
  - `UP046`/`UP047` - PEP 695 type parameters would rewrite `PaginatedResponse`, a shipped
    response schema FastAPI resolves through `response_model`. Mechanical, but out of scope here.
- **`known-third-party = ["alembic"]`:** the repo has a top-level `alembic/` script directory with
  no `__init__.py`, which ruff would otherwise read as a first-party package and sort against the
  order every generated revision is born with.
- **The Alembic template was fixed, not excluded.** Five of the nine findings came from
  `alembic/script.py.mako` leaking `typing.Sequence`/`Union` into `0001_initial.py`. Fixing the
  template means every future revision is born clean; the same fix was then applied once to the
  existing revision. `alembic/versions/` is linted and formatted like any other source - an
  excluded directory is a directory whose next migration nobody checks.
- **Type ignores are placed after formatting, not before.** `mypy --strict` leaves four findings,
  all idiom friction rather than defects: `Settings()` reads `database_url` and `jwt_secret` from
  the environment where mypy cannot see them, and the three admin listings hand ORM rows to
  `PaginatedResponse[...]`, which Pydantic validates on construction exactly as `response_model`
  does elsewhere. Each is a trailing `# type: ignore` on the formatted line. A comment placed
  inside a call would pin that call open forever; a trailing one leaves the formatter's own
  output untouched, and ruff exempts the pragma from `E501` by design. `warn_unused_ignores`
  is what stops the four comments outliving their reason.
- **Ruff and mypy are not in `requirements.lock`.** They are neither runtime nor test
  dependencies, and adding them would mean changing the lockfile's own documented regeneration
  command. They live in a `dev` extra, `==`-pinned there, which satisfies M0's pinning rule.
- **`.dockerignore` excludes `**/__pycache__`, not `__pycache__`.** The plan specified the bare
  name, which turns out to exclude only a root-level directory: Docker's ignore patterns are
  path-matched rather than recursive. The host's `alembic/__pycache__` and
  `alembic/versions/__pycache__` were shipping inside the runtime image, which made the image
  contents depend on whatever bytecode the build machine happened to have lying around. Nothing
  stale executes - source and `.pyc` are copied from the same host state, so Python's mtime/size
  check passes - but a build whose output varies with host state is not reproducible, which is
  the whole point of the line. `**/` matches at any depth and restores the intent. The pattern is
  worth knowing about because the bare form looks correct and fails silently.
- **nginx waits for the API to be healthy, not merely started.** The plan specified
  `depends_on: api`, whose short form means `service_started`. The API entrypoint runs
  `alembic upgrade head` before gunicorn binds its port, so "started" left a window - measured,
  not assumed - in which nginx was already accepting traffic and answering 502 to every request.
  The long form with `condition: service_healthy` closes it, and matches how the `api` service
  already waits on `postgres`. The cost is that `docker compose up` now blocks until the API is
  healthy instead of returning immediately; for a stack whose selling point is that `up` alone
  produces a working API, a slower `up` beats a fast one that briefly serves errors.
