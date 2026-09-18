"""The guard that keeps the suite off any database it has no business writing to.

M7's acceptance criterion - "running the suite does not touch dev data" - is
enforced by `assert_is_a_test_database` in conftest, so it is worth its own
tests: a guard that silently stopped working would be indistinguishable from a
guard that was never there until the day it mattered.
"""

import os

import pytest
from sqlalchemy.engine import make_url

from tests.conftest import (
    DEFAULT_TEST_DATABASE_URL,
    LEGACY_TEST_DATABASE,
    assert_is_a_test_database,
)

HOST = "postgresql+psycopg://postgres:postgres@localhost:5432"

ALLOWED = [
    DEFAULT_TEST_DATABASE_URL,
    f"{HOST}/hijack_takeaway_test",
    f"{HOST}/{LEGACY_TEST_DATABASE}",  # M0's database
    f"{HOST}/anything_test",  # another checkout's convention
]

REFUSED = [
    f"{HOST}/hijack_takeaway_dev",  # the one that actually bit M5
    f"{HOST}/hijack_takeaway_staging",
    f"{HOST}/postgres",
    f"{HOST}/production",
    f"{HOST}/test_but_not_at_the_end",  # `test` appearing anywhere is not enough
]


@pytest.mark.parametrize("url", ALLOWED)
def test_a_test_database_is_allowed(url: str) -> None:
    assert_is_a_test_database(url)  # does not raise


@pytest.mark.parametrize("url", REFUSED)
def test_anything_else_is_refused(url: str) -> None:
    with pytest.raises(RuntimeError, match="Refusing to run the test suite"):
        assert_is_a_test_database(url)


def test_the_refusal_names_the_database_and_the_way_out() -> None:
    with pytest.raises(RuntimeError) as raised:
        assert_is_a_test_database(f"{HOST}/hijack_takeaway_dev")

    message = str(raised.value)
    assert "hijack_takeaway_dev" in message  # which database was refused
    assert "docker-compose.test.yml" in message  # and what to do instead


def test_the_suite_is_running_against_a_test_database_right_now() -> None:
    """The guard, applied to this very run rather than to a literal."""
    url = os.environ["DATABASE_URL"]
    assert_is_a_test_database(url)
    assert make_url(url).database != "hijack_takeaway_dev"
