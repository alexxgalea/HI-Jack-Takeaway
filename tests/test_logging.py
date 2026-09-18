"""Structured logging and the redaction that guards it.

The load-bearing test here is `test_a_token_and_a_password_never_reach_the_log`:
it drives the two requests that actually carry credentials - a login with a
password in the form body, a call with a bearer token in the header - and reads
back every line the handler emitted.
"""

import io
import json
import logging
from collections.abc import Generator

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.core.logging import (
    REDACTED,
    JsonFormatter,
    RedactingFilter,
    configure_logging,
    redact,
    scrub_text,
)
from app.models.user import User
from tests.conftest import USER_PASSWORD
from tests.test_auth import auth_header

TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.fake-signature-value"


@pytest.fixture
def emitted() -> Generator[io.StringIO, None, None]:
    """Capture what the JSON handler writes, filter and all.

    A handler of our own rather than pytest's `caplog`: the question is what
    ends up in the *emitted* log, and redaction happens on the way through a
    handler. A capture that reads records before that point would prove nothing.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


def records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


# --- format ---------------------------------------------------------------


def test_every_record_is_one_json_object_per_line(emitted: io.StringIO) -> None:
    logging.getLogger("probe").warning("something happened")

    (record,) = records(emitted)
    assert record["level"] == "WARNING"
    assert record["logger"] == "probe"
    assert record["message"] == "something happened"
    assert record["timestamp"]


def test_extras_become_top_level_fields(emitted: io.StringIO) -> None:
    logging.getLogger("probe").info("ordered", extra={"order_id": 7})

    (record,) = records(emitted)
    assert record["order_id"] == 7


def test_a_traceback_is_logged_even_though_it_never_reaches_a_response(
    emitted: io.StringIO,
) -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging.getLogger("probe").exception("Unhandled exception")

    (record,) = records(emitted)
    assert "RuntimeError: boom" in str(record["exception"])


# --- redaction ------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["Authorization", "authorization", "Cookie", "Set-Cookie", "password", "token"],
)
def test_sensitive_keys_are_redacted_whatever_their_case(key: str) -> None:
    assert redact({key: "the-secret"}) == {key: REDACTED}


def test_redaction_reaches_into_nested_structures() -> None:
    scrubbed = redact({"headers": [{"Authorization": f"Bearer {TOKEN}"}]})
    assert scrubbed == {"headers": [{"Authorization": REDACTED}]}


def test_a_credential_in_a_plain_message_is_scrubbed() -> None:
    # No key to match on here - the regex pass is the only thing standing
    # between the token and the log file.
    assert TOKEN not in scrub_text(f"retrying with Bearer {TOKEN}")
    assert "s3cr3t" not in scrub_text('{"password": "s3cr3t"}')
    assert "s3cr3t" not in scrub_text("password=s3cr3t")


def test_sensitive_extras_are_redacted_on_the_way_out(emitted: io.StringIO) -> None:
    logging.getLogger("probe").info(
        "inbound",
        extra={
            "headers": {"Authorization": f"Bearer {TOKEN}", "Cookie": "session=abc"},
            "password": "s3cr3t",
            "path": "/auth/login",
        },
    )

    (record,) = records(emitted)
    assert record["headers"] == {"Authorization": REDACTED, "Cookie": REDACTED}
    assert record["password"] == REDACTED
    assert record["path"] == "/auth/login"  # an innocuous field is left alone
    assert TOKEN not in emitted.getvalue()


def test_configure_logging_installs_exactly_one_handler_however_often_it_runs() -> None:
    root = logging.getLogger()
    before = [h for h in root.handlers if h.get_name() != "hijack-json"]

    configure_logging()
    configure_logging()

    ours = [h for h in root.handlers if h.get_name() == "hijack-json"]
    assert len(ours) == 1
    # and it did not evict anybody else's handler on the way in
    assert before == [h for h in root.handlers if h.get_name() != "hijack-json"]


# --- request logging ------------------------------------------------------


async def test_a_request_is_logged_with_method_path_status_and_duration(
    client: AsyncClient, emitted: io.StringIO
) -> None:
    await client.get("/health")

    (record,) = [r for r in records(emitted) if r["logger"] == "app.request"]
    assert record["method"] == "GET"
    assert record["path"] == "/health"
    assert record["status_code"] == 200
    assert isinstance(record["duration_ms"], float)


async def test_a_failing_request_is_logged_with_its_status(
    client: AsyncClient, emitted: io.StringIO
) -> None:
    await client.get("/auth/me")  # no token -> 401

    (record,) = [r for r in records(emitted) if r["logger"] == "app.request"]
    assert record["status_code"] == 401


async def test_a_token_and_a_password_never_reach_the_log(
    client: AsyncClient, user: User, emitted: io.StringIO
) -> None:
    login = await client.post(
        "/auth/login", data={"username": user.email, "password": USER_PASSWORD}
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = await client.get("/auth/me", headers=auth_header(token))
    assert me.status_code == 200

    written = emitted.getvalue()
    assert written  # the requests really were logged
    assert USER_PASSWORD not in written
    assert token not in written
    assert "Bearer" not in written


async def test_request_logging_records_metadata_only(
    client: AsyncClient, db_session: Session, user: User, emitted: io.StringIO
) -> None:
    """No body is read into a record - not even a redacted one."""
    await client.post(
        "/auth/register",
        json={
            "email": "e2e-logging@example.com",
            "password": "a-good-password",
            "full_name": "Log Probe",
        },
    )

    (record,) = [r for r in records(emitted) if r["logger"] == "app.request"]
    assert set(record) == {
        "timestamp",
        "level",
        "logger",
        "message",
        "method",
        "path",
        "status_code",
        "duration_ms",
    }
    assert "a-good-password" not in emitted.getvalue()
