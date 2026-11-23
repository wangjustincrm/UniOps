"""sales36_audit_logs loses nothing (user 2026-11-23: remove nothing): from sales35, with rows in the tables it changes,
the upgrade keeps every row and value (count + md5 over each table, its new columns aside); then the append-only
rules hold (UPDATE / DELETE / TRUNCATE refused, INSERT works), break-glass works for its own transaction only and is
recorded; the property-change view reads the JSON; the downgrade refuses to cut a long summary. Ends at head."""
import sqlalchemy as sa

from tests.conftest import SYNC_URL, run_alembic

NEW = {"sales_audit": ["audit_log_id"], "sales_log": ["audit_log_id"], "sales_outbox": ["correlation_id"], "sales_connections": []}
LONG = "x" * 255


def _engine():
    return sa.create_engine(SYNC_URL)


def _fingerprint(conn) -> dict[str, tuple]:
    out = {}
    for t, extra in NEW.items():
        drop = "".join(f" - '{c}'" for c in extra)
        out[t] = tuple(conn.execute(sa.text(
            f"SELECT count(*), md5(coalesce(string_agg((to_jsonb(x){drop})::text, '' ORDER BY id), '')) FROM {t} x")).one())
    return out


def _seed(conn) -> None:
    for t in ("sales_audit", "sales_log", "sales_outbox", "sales_connections"):
        conn.execute(sa.text(f"DELETE FROM {t}"))
    conn.execute(sa.text("""
        INSERT INTO sales_audit (at, user_name, action, entity, entity_id, before, after, reason) VALUES
          (now(), 'Op', 'create', 'sales_orders', '1', NULL, '{"a": 1, "b": "x"}', 'made'),
          (now(), 'Op', 'update', 'sales_orders', '1', '{"a": 1}', '{"a": 2}', 'changed'),
          (now(), 'Op', 'delete', 'sales_document_links', '7', '{"entity_type": "order"}', 'null', 'unlinked')"""))
    conn.execute(sa.text("INSERT INTO sales_log (level, step, message, context) VALUES "
                         "('INFO', 'run', 'one', '{\"k\": 1}'), ('ERROR', 'edi', repeat('long ', 2000), NULL)"))
    conn.execute(sa.text("INSERT INTO sales_outbox (kind, payload, status, attempts, manual) VALUES ('send_940', '{\"o\": 1}', 'PENDING', 0, false)"))
    conn.execute(sa.text("INSERT INTO sales_connections (name, kind, after_read, created_by_name, version, last_run_summary) "
                         "VALUES ('c', 'SFTP', 'LEAVE', 't', 1, :s)"), {"s": LONG})


def _refused(conn, sql: str) -> bool:
    try:
        with conn.begin_nested():
            conn.execute(sa.text(sql))
        return False
    except sa.exc.DBAPIError as e:
        return "append-only" in str(e)


def test_sales36_keeps_every_row_and_value_and_then_is_append_only(migrated_db):  # noqa: ARG001
    run_alembic("downgrade", "sales35_order_settled", check=True)
    eng = _engine()
    try:
        with eng.begin() as c:
            _seed(c)
            before = _fingerprint(c)
        run_alembic("upgrade", "sales36_audit_logs", check=True)
        with eng.begin() as c:
            assert _fingerprint(c) == before  # nothing changed, nothing lost
            assert c.execute(sa.text("SELECT last_run_summary FROM sales_connections")).scalar() == LONG
        with eng.connect() as c:
            with c.begin():  # a header and an action to refuse changes to
                c.execute(sa.text("INSERT INTO sales_audit_logs (id, kind, correlation_id, execution_time) "
                                  "VALUES ('00000000-0000-0000-0000-000000000001', 'job', 'c1', now())"))
                c.execute(sa.text("INSERT INTO sales_audit_log_actions (audit_log_id, service_name, method_name, execution_time) "
                                  "VALUES ('00000000-0000-0000-0000-000000000001', 'S', 'm', now())"))
            with c.begin():
                for t in ("sales_audit", "sales_log", "sales_audit_logs", "sales_audit_log_actions"):
                    assert _refused(c, f"UPDATE {t} SET id = id"), t
                    assert _refused(c, f"DELETE FROM {t}"), t
                    assert _refused(c, f"TRUNCATE {t} CASCADE"), t  # CASCADE: the trigger, not the FK, refuses it
                c.execute(sa.text("INSERT INTO sales_log (level, step, message) VALUES ('INFO', 'x', 'appended')"))
            with c.begin():  # break-glass: this transaction only, and recorded
                c.execute(sa.text("SELECT sales_break_glass('test the glass', 'pytest')"))
                c.execute(sa.text("DELETE FROM sales_log WHERE message = 'appended'"))
            with c.begin():
                assert _refused(c, "DELETE FROM sales_log")  # the next transaction refuses again
                [row] = c.execute(sa.text("SELECT message, context FROM sales_log WHERE step = 'security'")).all()
                assert "pytest" in row[0] and row[1]["reason"] == "test the glass"
                props = c.execute(sa.text("SELECT property_name, original_value, new_value FROM sales_audit_property_changes "
                                          "WHERE change_type = 'update'")).all()
                assert [(p[0], p[1], p[2]) for p in props] == [("a", 1, 2)]
            with c.begin():  # a long summary: the downgrade refuses to cut it
                c.execute(sa.text("UPDATE sales_connections SET last_run_summary = :s"), {"s": "y" * 300})
        r = run_alembic("downgrade", "sales35_order_settled")
        assert r.returncode != 0 and "longer than 255" in ((r.stdout or "") + (r.stderr or ""))
    finally:
        eng.dispose()
        run_alembic("upgrade", "head", check=True)
