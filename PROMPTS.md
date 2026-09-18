## Prompts used

# Plan

1.  We're building a FastAPI takeaway platform. Key decisions are fixed in DECISIONS.md.
    Tech stack: FastAPI, PostgreSQL, SQLAlchemy, Alembic, Pydantic.
    Task: Create a step by step implementation plan with milestones (e.g. , scaffolding, models, auth, orders, status transitions, admin endpoints). For each milestone: files to create, key functions & classes, acceptance criteria. Propose a suggested order of execution that allows running and testing incrementally. Keep it concise (bullet points, no prose). Stop after the plan, no implementation.

2.  Review note: after reviewing the implementation plan: i am locking on the auth approach
    Decision: Custom JWT with PyJWT, not fastapi-users
    Rationale: default FastAPI choice, minimal dependencies (pyjwt & pwdlib), fullcontrol over token payload, expiration, role checks, keeps it simple
    Requirements: OAuth2PasswordBearer(tokenUrl="/auth/login") for token extraction, pyjwt for encoding/decoding tokens with algorithms=["HS256"],
    pwdlib for one-way password hashing (Argon2 or bcrypt), get_current_user dependency: decode JWT → validate exp/sub → load user → raise 401 on failure, require_admin dependency: check user.role == "admin", Access tokens short-lived (15–30 min)
    Update the plan to reflect this auth approach (files, functions, acceptance criteria); Do not implement yet - just revise the milestones

3.  Yes, update DECISIONS.md to reflect the auth decisions.

4.  Review: after reviewing implementation again: small refinements:
    Decisions:

- Freeze versions with pyproject.toml
- Add pytest-asyncio & httpx to test deps
- Revise the aliases in M2: CurrentUser & AdminUser -> type aliases:
  from typing import Annotated
  CurrentUser = Annotated[User, Depends(get_current_user)]
  AdminUser = Annotated[User, Depends(require_admin)]
- Explicit note about python-multipart requirement (OAuth2PasswordRequestForm requires python-multipart)
- docker-compose.test.yml for CI in M7
- 8 extra git branches in plan, mapped to the milestones and planning step:
  - plan/initial
  - feat/00-scaffolding
  - feat/01-models-migration
  - feat/02-auth
  - feat/03-restaurants
  - feat/04-orders
  - feat/05-transitions
  - feat/06-admin
  - feat/07-hardening
- main branch protected: block direct commit
  Update the plan to reflect the decisions - just revise the milestones, do not implement

# Build

# Review

# Explain
