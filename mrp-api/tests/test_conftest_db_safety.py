"""Guards `conftest._migrate()`'s DROP SCHEMA CASCADE against ever running
against a database whose name doesn't look like a test database.

This repo has a documented history of a host .env pointing POSTGRES_* at the
shared production DB (feedback_uniops_host_env_points_at_prod) — TEST_MRP_DB
is env-controlled the same way, so a missing/typo'd override must fail
loudly before touching any connection, not silently DROP SCHEMA on whatever
database the name happens to resolve to.
"""
import pytest

import conftest as conftest_module


def test_migrate_refuses_non_test_database_name(monkeypatch):
    monkeypatch.setattr(conftest_module, "TEST_DB", "epms")  # NOT a _test db
    with pytest.raises(RuntimeError, match="_test"):
        conftest_module._migrate()


def test_migrate_refuses_production_lookalike_name(monkeypatch):
    monkeypatch.setattr(conftest_module, "TEST_DB", "mrp_production")
    with pytest.raises(RuntimeError, match="_test"):
        conftest_module._migrate()
