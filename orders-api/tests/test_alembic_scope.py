"""Autogenerate sees only this service's tables (the database is shared): a
foreign table is ignored, an `obr_` table the models do not know is not."""
import sqlalchemy as sa

from tests.conftest import SYNC_URL, run_alembic


def _exec(sql: str) -> None:
    eng = sa.create_engine(SYNC_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(sa.text(sql))
    eng.dispose()


def test_autogenerate_ignores_other_services_tables(migrated_db):
    _exec("CREATE TABLE IF NOT EXISTS zz_foreign_service (id int primary key)")
    try:
        r = run_alembic("check")
        assert r.returncode == 0, r.stdout
        assert "No new upgrade operations detected" in r.stdout
    finally:
        _exec("DROP TABLE IF EXISTS zz_foreign_service")


def test_autogenerate_still_sees_its_own_prefix(migrated_db):
    _exec("CREATE TABLE IF NOT EXISTS obr_zz_probe (id int primary key)")
    try:
        r = run_alembic("check")
        assert r.returncode != 0 and "obr_zz_probe" in r.stdout, r.stdout
    finally:
        _exec("DROP TABLE IF EXISTS obr_zz_probe")
