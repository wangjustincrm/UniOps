"""Integration tests share the platform tests' database fixtures (their own
`pt_` tables in the `_test` database)."""
from tests.platform.conftest import (  # noqa: F401 — fixtures
    clean, person, platform_tables, session_factory, stranger, uow_factory,
)
