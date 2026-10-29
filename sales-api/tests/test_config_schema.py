"""sales01_config: the configuration tables exist, enforce their closed value
sets and the EA = 1 rule (FR-OB-02), and the migration goes down and up again.
Each refusal has its admitted twin, so "everything is refused" cannot pass."""
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from tests.conftest import SYNC_URL, run_alembic

TABLES = {
    "sales_partners", "sales_items", "sales_item_units", "sales_item_partner_codes",
    "sales_dc_routing", "sales_warehouses", "sales_connections", "sales_settings",
    "sales_notification_rules",
}
AUDIT = "created_by_name"


@pytest.fixture
def conn(migrated_db):
    eng = sa.create_engine(SYNC_URL)
    with eng.connect() as c:
        tx = c.begin()
        yield c
        tx.rollback()
    eng.dispose()


def _tables() -> set[str]:
    eng = sa.create_engine(SYNC_URL)
    try:
        return {t for t in sa.inspect(eng).get_table_names() if t.startswith("sales_")}
    finally:
        eng.dispose()


def _item(c, code="CF0001"):
    c.execute(sa.text(
        "INSERT INTO sales_items (item_code, warehouse_held, version, created_by_name) "
        "VALUES (:c, false, 1, 'test')"), {"c": code})


def _unit(c, unit, factor, code="CF0001"):
    c.execute(sa.text(
        "INSERT INTO sales_item_units (item_code, unit, factor_ea, version, created_by_name) "
        "VALUES (:c, :u, :f, 1, 'test')"), {"c": code, "u": unit, "f": factor})


def test_the_nine_config_tables_exist(migrated_db):
    assert TABLES <= _tables()


def test_ea_factor_must_be_one(conn):
    _item(conn)
    _unit(conn, "EA", 1)
    _unit(conn, "CASE", 12)  # the positive half: other units carry their own factor
    with pytest.raises(IntegrityError, match="ck_sales_item_units_ea_is_one"):
        with conn.begin_nested():
            _unit(conn, "EA", 2, code="CF0001")


def test_unit_needs_a_known_item(conn):
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            _unit(conn, "CASE", 12, code="NOPE")


def _partner(c, channel):
    c.execute(sa.text(
        "INSERT INTO sales_partners (code, name, channel, settlement_mode, sending_mode, "
        "delay_minutes, invoice_method, dc_routing_required, version, created_by_name) "
        "VALUES (:code, 'P', :ch, 'INVOICE', 'AUTOMATIC', 0, 'EMAIL', false, 1, 'test')"),
        {"code": f"P-{channel}", "ch": channel})


def test_partner_channel_is_a_closed_set(conn):
    _partner(conn, "TRUECOMMERCE")
    with pytest.raises(IntegrityError, match="ck_sales_partners_channel"):
        with conn.begin_nested():
            _partner(conn, "FAX")


def test_connection_stable_seconds_defaults_to_60(conn):
    conn.execute(sa.text(
        "INSERT INTO sales_connections (name, kind, after_read, mode, version, created_by_name) "
        "VALUES ('radiant', 'SFTP', 'LEAVE', 'TEST', 1, 'test')"))
    assert conn.execute(sa.text(
        "SELECT stable_seconds FROM sales_connections WHERE name = 'radiant'")).scalar() == 60


def test_downgrade_removes_the_tables_and_upgrade_restores_them(migrated_db):
    try:
        r = run_alembic("downgrade", "base")
        assert r.returncode == 0, r.stdout
        assert not (TABLES & _tables())
    finally:
        r = run_alembic("upgrade", "head")
        assert r.returncode == 0, r.stdout
    assert TABLES <= _tables()
