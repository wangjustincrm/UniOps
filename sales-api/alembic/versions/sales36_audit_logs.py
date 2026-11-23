"""sales36_audit_logs — the audit log ≈ ABP's AbpAuditLogs / AbpAuditLogActions, its link from the entity changes and
the business log, the correlation id on the outbox, and append-only audit / log tables (user 2026-11-23).

- sales_audit_logs: one per HTTP request and per saved background scope (job run, outbox send); id = a UUID made by
  the app at scope start (as ABP's Guid keys), so the entity-change rows are stamped while the work runs.
- sales_audit_log_actions: the service actions of a log, parameters whole (the user's rule "remove nothing").
- sales_audit.audit_log_id, sales_log.audit_log_id: nullable, indexed, NO foreign key — the entity changes commit in
  the work's transaction, the header in its own session at the scope's end (≈ ABP's requiresNew), so the header may
  come after its children (and is never written for a scope the options do not save).
- sales_outbox.correlation_id: the request's id carried to the send.
- sales_connections.last_run_summary varchar(255) → text (the code stops cutting it at 255).
- VIEW sales_audit_property_changes ≈ AbpEntityPropertyChanges, derived from sales_audit's before / after (no copy).
- Append-only: UPDATE, DELETE and TRUNCATE on sales_audit, sales_log, sales_audit_logs, sales_audit_log_actions raise,
  unless the transaction set `sales.break_glass` — which `sales_break_glass(reason, who)` does AND records in sales_log.

Existing rows are not changed (new columns are NULL; the varchar→text change keeps every value).

Revision id "sales36_audit_logs" is 18 characters (limit 32).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "sales36_audit_logs"
down_revision = "sales35_order_settled"
branch_labels = None
depends_on = None

APPEND_ONLY = ("sales_audit", "sales_log", "sales_audit_logs", "sales_audit_log_actions")


def upgrade() -> None:
    op.create_table(
        "sales_audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("application_name", sa.String(40), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_name", sa.String(255), nullable=True),
        sa.Column("role", sa.String(60), nullable=True),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column("browser_info", sa.Text(), nullable=True),
        sa.Column("http_method", sa.String(10), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("route", sa.String(255), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("execution_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("exceptions", sa.Text(), nullable=True),
        sa.Column("comments", postgresql.JSONB(), nullable=True),
        sa.Column("entity_changes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extra", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint("kind IN ('request', 'job', 'outbox', 'intake', 'cli')", name="ck_sales_audit_logs_kind"),
    )
    op.create_index("ix_sales_audit_logs_execution_time", "sales_audit_logs", [sa.text("execution_time DESC")])
    op.create_index("ix_sales_audit_logs_correlation_id", "sales_audit_logs", ["correlation_id"])
    op.create_index("ix_sales_audit_logs_user_time", "sales_audit_logs", ["user_id", "execution_time"])
    op.create_index("ix_sales_audit_logs_failed", "sales_audit_logs", ["http_status"],
                    postgresql_where=sa.text("http_status >= 400"))

    op.create_table(
        "sales_audit_log_actions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("audit_log_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sales_audit_logs.id", name="fk_sales_audit_log_actions_log"), nullable=False),
        sa.Column("service_name", sa.String(255), nullable=False),
        sa.Column("method_name", sa.String(128), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=True),
        sa.Column("execution_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_duration_ms", sa.Integer(), nullable=True),
    )
    op.create_index("ix_sales_audit_log_actions_log", "sales_audit_log_actions", ["audit_log_id"])

    op.add_column("sales_audit", sa.Column("audit_log_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_sales_audit_audit_log_id", "sales_audit", ["audit_log_id"])
    op.add_column("sales_log", sa.Column("audit_log_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_sales_log_audit_log_id", "sales_log", ["audit_log_id"])
    op.add_column("sales_outbox", sa.Column("correlation_id", sa.String(64), nullable=True))
    op.alter_column("sales_connections", "last_run_summary", type_=sa.Text(), existing_type=sa.String(255),
                    existing_nullable=True)

    # ≈ AbpEntityPropertyChanges: one row per property of each change, from the JSON (no copy of the data).
    op.execute("""
        CREATE VIEW sales_audit_property_changes AS
        SELECT a.id AS entity_change_id, a.audit_log_id, a.at AS change_time, a.action AS change_type,
               a.entity AS entity_type, a.entity_id, k.property_name,
               a.before -> k.property_name AS original_value, a.after -> k.property_name AS new_value
        FROM sales_audit a
        CROSS JOIN LATERAL (
            SELECT jsonb_object_keys(CASE WHEN jsonb_typeof(a.after) = 'object' THEN a.after ELSE '{}'::jsonb END)
            UNION
            SELECT jsonb_object_keys(CASE WHEN jsonb_typeof(a.before) = 'object' THEN a.before ELSE '{}'::jsonb END)
        ) AS k(property_name)
    """)

    # Append-only, with a recorded break-glass (precedent: redact_card_documents, 2026-11-04).
    op.execute("""
        CREATE FUNCTION sales_refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF coalesce(current_setting('sales.break_glass', true), '') = '' THEN
                RAISE EXCEPTION '% on % is refused: the table is append-only (sales_break_glass(reason, who) first)',
                    TG_OP, TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION sales_break_glass(reason text, who text) RETURNS void LANGUAGE plpgsql AS $$
        BEGIN
            IF coalesce(trim(reason), '') = '' OR coalesce(trim(who), '') = '' THEN
                RAISE EXCEPTION 'break-glass needs a reason and who';
            END IF;
            PERFORM set_config('sales.break_glass', reason, true);  -- this transaction only
            INSERT INTO sales_log (level, step, message, context)
            VALUES ('WARNING', 'security', 'break-glass on the append-only tables by ' || who || ': ' || reason,
                    jsonb_build_object('who', who, 'reason', reason, 'tables', 'sales_audit, sales_log, sales_audit_logs, sales_audit_log_actions'));
        END $$
    """)
    for t in APPEND_ONLY:
        op.execute(f"CREATE TRIGGER trg_{t}_append_only BEFORE UPDATE OR DELETE ON {t} "
                   "FOR EACH ROW EXECUTE FUNCTION sales_refuse_change()")
        op.execute(f"CREATE TRIGGER trg_{t}_no_truncate BEFORE TRUNCATE ON {t} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION sales_refuse_change()")


def downgrade() -> None:
    for t in APPEND_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{t}_no_truncate ON {t}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{t}_append_only ON {t}")
    op.execute("DROP FUNCTION IF EXISTS sales_break_glass(text, text)")
    op.execute("DROP FUNCTION IF EXISTS sales_refuse_change()")
    op.execute("DROP VIEW IF EXISTS sales_audit_property_changes")
    # Refuses to cut a summary longer than 255 (remove nothing): shorten nothing, stop instead.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM sales_connections WHERE length(last_run_summary) > 255) THEN
                RAISE EXCEPTION 'a last_run_summary is longer than 255: downgrade refused (it would cut it)';
            END IF;
        END $$
    """)
    op.alter_column("sales_connections", "last_run_summary", type_=sa.String(255), existing_type=sa.Text(),
                    existing_nullable=True)
    op.drop_column("sales_outbox", "correlation_id")
    op.drop_index("ix_sales_log_audit_log_id", table_name="sales_log")
    op.drop_column("sales_log", "audit_log_id")
    op.drop_index("ix_sales_audit_audit_log_id", table_name="sales_audit")
    op.drop_column("sales_audit", "audit_log_id")
    op.drop_index("ix_sales_audit_log_actions_log", table_name="sales_audit_log_actions")
    op.drop_table("sales_audit_log_actions")
    for ix in ("ix_sales_audit_logs_failed", "ix_sales_audit_logs_user_time", "ix_sales_audit_logs_correlation_id",
               "ix_sales_audit_logs_execution_time"):
        op.drop_index(ix, table_name="sales_audit_logs")
    op.drop_table("sales_audit_logs")
