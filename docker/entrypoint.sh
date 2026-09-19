#!/bin/sh
# Migrations run here rather than by hand, so nothing manual sits between
# `docker compose up` and a working API. `set -e` makes a failed migration stop
# the container instead of starting a server against a half-built schema.
set -e

alembic upgrade head

# uvicorn_worker.UvicornWorker, not uvicorn.workers.UvicornWorker: on the
# pinned uvicorn 0.53.0 the in-tree module still imports but raises a
# DeprecationWarning pointing at the uvicorn-worker package, which is what this
# uses. --forwarded-allow-ips='*' is safe only because the api service
# publishes no host port and nginx is the sole ingress.
exec gunicorn app.main:app \
    --worker-class uvicorn_worker.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --workers "${WEB_CONCURRENCY:-2}" \
    --forwarded-allow-ips='*' \
    --access-logfile - \
    --error-logfile -
