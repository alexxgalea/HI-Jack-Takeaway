# Takeaway Platform Backend

Minimal FastAPI backend for a takeaway platform: customers browse restaurants & menus, place orders, and track order status. Admins manage menu items and update order statuses.

## Tech Stack

- **Framework:** FastAPI
- **Database:** PostgreSQL
- **ORM:** SQLAlchemy 2.0
- **Migrations:** Alembic
- **Validation:** Pydantic v2
- **Auth:** JWT (PyJWT + pwdlib/Argon2)

## Key Design Decisions

See [DECISIONS.md](DECISIONS.md) for architecture choices, data model, and out-of-scope features.

## Implementation Plan

See [PLAN.md](PLAN.md) for milestones, acceptance criteria, and execution order.

## Quick Start (after scaffolding)

```bash
# Start Postgres
docker compose up -d

# Run migrations
alembic upgrade head

# Start server
uvicorn app.main:app --reload

# Run tests
pytest
```

## Status

- [ ] M0: Scaffolding
- [ ] M1: Models + migrations
- [ ] M2: Auth
- [ ] M3: Restaurants & menu
- [ ] M4: Order placement
- [ ] M5: Status transitions
- [ ] M6: Admin endpoints
- [ ] M7: Hardening + CI

---

_Protected branch: `main`. All changes via PR._
