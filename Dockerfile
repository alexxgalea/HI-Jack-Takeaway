# Two stages so the runtime image carries no compiler, no pip cache and no
# source tree: only a built virtualenv, the migrations, and the entrypoint.

# ---------- builder ----------
FROM python:3.12-slim AS builder

# The venv is the unit that crosses the stage boundary, so everything the
# runtime needs has to be installed into it rather than the system Python.
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /src

# Lockfile first and on its own layer: it changes far less often than the
# application code, so edits to app/ reuse this install.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Non-editable, so the `app` package lands in site-packages. The runtime stage
# then needs neither the source tree nor pip. --no-deps because requirements.lock
# already put every dependency in place at its pinned version; letting pip
# re-resolve here would be the one place the lockfile could be bypassed.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir --no-deps .

# ---------- runtime ----------
FROM python:3.12-slim AS runtime

COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 app

WORKDIR /app

# alembic.ini resolves `script_location = %(here)s/alembic` next to itself, so
# the two have to keep this layout. alembic/env.py imports app.core.config and
# app.db.base, which resolve from the venv rather than from this directory.
COPY alembic.ini ./
COPY alembic ./alembic
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# Belt and braces: the repository file is committed executable, but a checkout
# on a filesystem that drops the bit would otherwise fail at `docker compose up`.
RUN chmod +x /usr/local/bin/entrypoint.sh

USER app

EXPOSE 8000

ENTRYPOINT ["entrypoint.sh"]
