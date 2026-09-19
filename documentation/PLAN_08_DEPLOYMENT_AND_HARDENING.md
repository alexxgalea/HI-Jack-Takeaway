# M8 - Deployment (plan amendment)

An **amendment** to [PLAN.md](PLAN.md), not a replacement. M0-M7 stand as written and as
shipped. Scope here is bounded by the challenge brief's stack list; anything the brief does
not name is deferred, and named as deferred rather than left silent.

**Branch:** `feat/08-deployment-critical`, branched from `main`, PR-merged, same rules as every
prior milestone (own acceptance checklist in the PR description, CI green before merge).

**This milestone does not change core business logic.** Order status transitions, price
snapshotting, `total_amount`, the JWT auth flow, role checks, routes, response models, response
shapes and the database schema are untouched. The only edits to `app/` are three
`# type: ignore` comments in §5. No unrelated refactoring; do not silently expand scope.

The pins and the exact lint/type findings below were established by a dry run on 2026-09-19,
not estimated. Where this document states a count or a version, it was measured.

## Scope against the brief

| Brief's stack item | State |
| --- | --- |
| FastAPI | Met - M0 |
| PostgreSQL | Met - M0/M1 |
| SQLAlchemy + Alembic | Met - M1 |
| Pydantic request/response models | Met - M2-M6 |
| JWT auth | Met - M2 |
| **Docker & docker-compose** | **Gap - compose starts Postgres only** |
| **Nginx reverse proxy in front of Uvicorn/Gunicorn** | **Gap - absent entirely** |

Two gaps, both named explicitly in the brief, both currently unmentioned in
[DECISIONS.md](DECISIONS.md)'s *Out of scope* list. §5 (lint and type gates) is not named in the
brief; it is kept as a must-have because the repo already runs CI the brief never asked for, and
a config-only gate on existing code carries no product risk.

---

## 0. Before editing: re-confirm these repository facts

Each was confirmed on 2026-09-19 and the expected answer is given, so a deviation is detectable
rather than silent. **If any differs, adapt the implementation to the repository and report the
deviation. Do not invent migration IDs, service names or commands.**

| Fact | Expected | Where |
| --- | --- | --- |
| Alembic head | `0001` (`down_revision = None`; single revision) | `alembic/versions/0001_initial.py` |
| Health endpoint | `GET /health` -> `{"status": "ok"}`, defined inline in `create_app()` | [app/main.py](../app/main.py) |
| Postgres compose service | service `postgres`, db `hijack_takeaway`, port 5432 | `docker-compose.yml` |
| Lockfile regeneration | fresh venv, then `pip install ".[test]" && pip freeze` | header of `requirements.lock` |
| CI package install | `pip install -r requirements.lock` then `pip install -e . --no-deps` | `.github/workflows/ci.yml` |

- [ ] **Use the actual `/health` response in the acceptance checks.** If it turns out to differ
      from `{"status": "ok"}`, test what it returns - do not modify the endpoint to match an
      assumed shape.

## 1. Docker & docker-compose for the API

Today `docker compose up` starts Postgres only, so running this project takes five manual steps.

**Add**
- [ ] `Dockerfile` (repo root), multi-stage:
      *builder* `python:3.12-slim` -> `python -m venv /opt/venv`,
      `pip install -r requirements.lock`, then `pip install . --no-deps` (non-editable, so the
      `app` package lands in site-packages and the runtime stage needs no source tree or pip).
      *runtime* `python:3.12-slim` -> `COPY --from=builder /opt/venv /opt/venv`,
      `ENV PATH=/opt/venv/bin:$PATH`, non-root `app` user, `WORKDIR /app`, `EXPOSE 8000`.
- [ ] Runtime stage must also carry `alembic.ini` and `alembic/` into `/app`:
      `script_location = %(here)s/alembic` resolves next to the ini, and `alembic/env.py`
      imports `app.core.config` and `app.db.base`, which resolve from the venv.
- [ ] `.dockerignore` (repo root) - `.git`, `.venv`, `__pycache__`, `.pytest_cache`, `htmlcov`,
      `.env`, `documentation`, `tests`.
- [ ] `docker/entrypoint.sh` - `set -e`; `alembic upgrade head`; then
      `exec gunicorn app.main:app --worker-class uvicorn_worker.UvicornWorker --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-2}" --forwarded-allow-ips='*' --access-logfile - --error-logfile -`.
      Migrations run here so nothing manual sits between `up` and a working API.
- [ ] **`uvicorn_worker.UvicornWorker`, not `uvicorn.workers.UvicornWorker`.** On the pinned
      uvicorn 0.53.0 the in-tree module still imports but raises
      `DeprecationWarning: the uvicorn.workers module is deprecated, use the uvicorn-worker
      package instead`. Use the maintained package.
- [ ] `docker-compose.yml` - new `api` service: `build: .`;
      `depends_on: postgres: {condition: service_healthy}`;
      `DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/hijack_takeaway`
      (service name, not `localhost`);
      `JWT_SECRET: ${JWT_SECRET:-dev-only-not-for-deployment}` so a fresh clone with no `.env`
      still boots; **no `ports:` entry** - nginx is the only ingress.
- [ ] `api` healthcheck: `python:3.12-slim` ships no curl, so use
      `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"`
      rather than adding a package for it.

**Dependencies - do not blindly regenerate the lockfile**
- [ ] `pyproject.toml` `[project.dependencies]` += `gunicorn==26.2.0`, `uvicorn-worker==0.4.0`.
- [ ] Regenerate with **the repository's own documented command** - a fresh venv, then
      `pip install ".[test]" && pip freeze` - not a variant of it. Verify every pinned version
      resolves for Python 3.12 and that regenerating twice produces an identical file.
- [ ] **Do not update unrelated dependencies.** The expected `requirements.lock` diff is two
      added lines, `gunicorn==26.2.0` and `uvicorn-worker==0.4.0`: `packaging` (gunicorn's
      dependency) and `uvicorn` (uvicorn-worker's) are already pinned in the lock. Any other
      line moving means the resolver drifted - investigate before committing.
- [ ] Ruff and mypy stay **out** of the lockfile: they are not runtime or test dependencies, and
      adding them would mean changing the documented regeneration command. They live in a `dev`
      extra, `==`-pinned there, which satisfies M0's pinning rule (see §5).

**Base images**
- [ ] `python:3.12-slim` and `nginx:1-alpine` are both floating minor-version tags, which is one
      consistent choice - record it in DECISIONS.md as deliberate. **Do not introduce digest
      pinning:** it would not match the existing repository style, and for a tech challenge
      consistency and a simple reviewer experience matter more than byte-exact image identity.

**Acceptance**
- [ ] From a clean clone with only Docker installed, and from a clean volume:
      `docker compose down -v` then `docker compose up --build` serves a working API - no venv,
      no `.env`, no separate `alembic upgrade head`. The `down -v` is not optional: an existing
      volume hides migration and first-boot failures.
- [ ] A second `docker compose up` against the now-existing volume is a no-op migration.
- [ ] `docker-compose.test.yml` is unchanged and still runs the suite, unless a repository fact
      makes that impossible - in which case report it rather than working around it.

## 2. Nginx reverse proxy in front of Gunicorn

**Add**
- [ ] `docker/nginx/default.conf` - `upstream api { server api:8000; }`;
      `location / { proxy_pass http://api; }` with `Host`, `X-Real-IP`, `X-Forwarded-For`,
      `X-Forwarded-Proto` set; `client_max_body_size 1m`; proxy connect/read timeouts.
- [ ] `docker-compose.yml` - `nginx` service: `nginx:1-alpine`, config bind-mounted read-only
      at `/etc/nginx/conf.d/default.conf`, `depends_on: api`, publishes `8080:80`
      (not `80`, which needs root on macOS). Nginx must be the **only** published application
      service.
- [ ] Gunicorn carries `--forwarded-allow-ips='*'` from §1. This is safe **only** because the
      API publishes no host port and nginx is the sole ingress. Write that reasoning into
      DECISIONS.md and the README - it is what shows the flag was a decision rather than
      something lifted from a template.

**Acceptance**
- [ ] `curl -i localhost:8080/health` -> 200 with the endpoint's real body and a `Server: nginx`
      header.
- [ ] `localhost:8080/docs` renders and its Authorize flow issues a working token.
- [ ] `curl localhost:8000/health` from the host **fails** - the API is not directly reachable,
      which is what makes the proxy real rather than decorative.

## 5. Lint and type gates in CI

CI runs migrations, tests and coverage; there is no ruff or mypy config in the repo and no gate
on either. The dry run found the code close to clean - 9 ruff findings and 5 mypy errors, none
of them defects.

**Add - ruff**
- [ ] `[tool.ruff]` `line-length = 100`, `target-version = "py312"`.
      **Not 88.** The codebase is hand-formatted in a Black-ish style at roughly 90 columns and
      matches neither width exactly: at 88, `ruff format` mangles [main.py:11](../app/main.py#L11)
      into `settings = (\n    get_settings()\n)  # fail fast ...`; at 100 every change is a pure
      line-join and the author's own lines are left alone.
- [ ] `[tool.ruff.lint] select = ["E", "F", "I", "UP", "B", "SIM"]` with a reasoned `ignore`:
      - `E501` - ruff's own guidance for formatter users; `ruff format` owns line length and
        cannot split long comments or generated strings.
      - `UP042` (2 hits, `app/models/enums.py`) - `(str, Enum)` -> `StrEnum` changes what
        `str(UserRole.user)` returns, from `"UserRole.user"` to `"user"`. That is a behaviour
        change, not a refactor, and it does not belong in a deployment branch.
      - `UP046`/`UP047` (2 hits) - PEP 695 type parameters for
        `PaginatedResponse(BaseModel, Generic[T])` and the `_page` helper. Mechanical, but it
        rewrites a shipped response schema that FastAPI resolves through `response_model`.
- [ ] `[tool.ruff.lint.isort] known-third-party = ["alembic"]`. Without it ruff sees the repo's
      top-level `alembic/` directory - a script location with no `__init__.py`, not a package -
      and sorts `from alembic import op` into the first-party block, fighting the import order
      every generated revision is born with.
- [ ] Fix `alembic/script.py.mako`, do not exclude the directory. 4 of the 9 findings are in
      `alembic/versions/0001_initial.py` and come straight from the template's
      `from typing import Sequence, Union` (UP035, UP007 x3) and unsorted imports. Fixing the
      template makes every future revision born clean; then apply the same header fix once to
      `0001_initial.py`. The 5th finding is a genuine import sort in `alembic/env.py`.
- [ ] One `style: apply ruff format` commit, kept separate from the functional commits:
      **23 files**, all in `app/` and `tests/`, all line-joins. Verify the diff changed
      formatting only.

**Add - mypy**
- [ ] `[tool.mypy]` `python_version = "3.12"`, `strict = true`, `warn_unused_ignores = true`.
      Full strict is reachable; `mypy app` reports exactly 5 errors, in 2 files, both idiom
      frictions rather than defects:
      - `app/core/config.py` (2) - `Settings()` missing `database_url`/`jwt_secret`.
        pydantic-settings reads them from the environment and mypy cannot see it. One
        `# type: ignore[call-arg]`.
      - `app/api/routers/admin.py` (3) - `PaginatedResponse[OrderOut](items=<ORM rows>)`.
        Pydantic validates the ORM rows into `OrderOut` on construction, exactly as
        `response_model` does elsewhere. Three `# type: ignore[arg-type]`, placed on the
        exploded `items=` argument so `ruff format` leaves them where they belong.
      `warn_unused_ignores` is what stops those four comments outliving their reason.
- [ ] CI target is `mypy app`, which never reaches `alembic/`. An
      `[[tool.mypy.overrides]] module = "alembic.versions.*"` is only worth adding if local
      `mypy .` runs are expected - decide once, do not add it unused.
- [ ] `[project.optional-dependencies] dev = ["ruff==0.16.8", "mypy==2.3.1"]`, installed by the
      lint job with `pip install -e ".[dev]" --no-deps`. Both are `==`-pinned here rather than in
      `requirements.lock` - see the dependency note in §1.

**Add - CI**
- [ ] `.github/workflows/ci.yml` - new `lint` job, same checkout/setup steps as `test`, running
      `ruff check .`, `ruff format --check .`, `mypy app`. It needs no database.
- [ ] Add `lint` to the required status checks on protected `main`.

**Acceptance**
- [ ] `ruff check .`, `ruff format --check .` and `mypy app` all exit 0 locally and in CI.
- [ ] A deliberately unused import fails the PR.
- [ ] The existing test and coverage commands still pass unchanged - the formatting commit
      changed no behaviour.

---

## Order of work

1. §5 first - the gates land on the code as it is today, so §1 and §2 are written under them.
2. §1 - dependencies and lockfile, Dockerfile, entrypoint, `api` service.
3. §2 - nginx has nothing to proxy to until the API image exists.
4. Docs: README gains a *Run with Docker* section as the primary path; DECISIONS.md records the
   Docker/nginx/lint decisions, the floating-tag choice, the `--forwarded-allow-ips` reasoning
   and the deferred list, and corrects its *Out of scope* section; PLAN.md's branching table
   gains the `feat/08-deployment-critical` row.

## Validate progressively

Not only at the end - each step's failure should be attributable to the step that caused it.

```
ruff check . && ruff format --check . && mypy app
pytest                       # the repo's existing test and coverage commands, unchanged
docker compose config        # before building anything
docker compose down -v       # clean volume, so first boot is really first boot
docker compose up --build
curl -i localhost:8080/health            # through nginx: 200, Server: nginx
curl -sf localhost:8000/health && echo UNEXPECTED   # must fail: no host port on api
docker compose up            # second start: migrations are a no-op
```

Milestone is done when all of the above pass and a clean clone serves `localhost:8080/docs` from
`docker compose up --build` alone.

## Report at the end

Files changed; commands run and their results; acceptance criteria met; **any deviation from
this plan** (especially anything from §0 that did not match); any remaining risks.

## Deferred - known improvements, not brief requirements

Real findings, none of them named in the brief. They stay out so the deployment milestone stays
focused; each is an interview talking point rather than a pass/fail criterion. Record them, do
not implement them, and do not modify their behaviour.

- [ ] Record these in DECISIONS.md as *known improvements, deferred to keep the deployment
      milestone focused* - the original mistake was silence, not the deferral.

**§3 - registration input validation.** `{"password": ""}` returns 201 and creates an account
that can never authenticate, with no password reset in scope. `Case@X.com` and `case@x.com` both
register, because the unique index is byte-exact. Also: `full_name` accepts `" "`, and
`POST /auth/register`'s check-then-insert race surfaces as a 500 rather than the documented 409.
Fix would be `Field(min_length=8)`, a lowercasing validator applied at register and at login
lookup, and `try/except IntegrityError` around the commit.

**§4 - indexes and FK hygiene.** Postgres does not auto-index foreign keys; `ix_users_email` is
the only non-PK index in the schema, so every listing is a sequential scan. One Alembic revision
would add composites on `orders (customer_id, created_at DESC, id DESC)` and
`orders (restaurant_id, created_at DESC, id DESC)` - whose leading column also serves the FK
lookup - plus single-column indexes on `order_items (order_id)`,
`order_items (restaurant_item_id)` and `restaurant_items (restaurant_id)`. Same revision is where
FK `ondelete` rules and `CHECK` constraints mirroring the existing Pydantic rules would go.

**Others.** Two pagination contracts (`GET /orders` returns a bare list, `/admin` returns
`PaginatedResponse`) - unifying them is a breaking response-shape change. `GET /restaurants` is
unpaginated. `seed.py` has 0% coverage. TLS termination at nginx needs a certificate and a real
hostname; the proxy already passes `X-Forwarded-Proto`, so adding it later is config, not code.
