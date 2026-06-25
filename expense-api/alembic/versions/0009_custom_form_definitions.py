"""Custom form definitions table + backfill from policy JSON (PRD-OA §10.1).

Migrates CFM out of expense_policy_config.custom_forms (JSONB array) into a
dedicated custom_form_definitions table. The policy.custom_forms column is
retained for now as a rollback safety net; it is dropped in a later migration
once the table is verified in production.

Idempotent: skips table creation if it already exists; backfill skips codes
that are already present.

Revision ID: 0009
Revises: 0008
"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def _table_exists(conn, table: str) -> bool:
    res = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=:t"
    ), {"t": table})
    return bool(res.first())


def upgrade() -> None:
    conn = op.get_bind()

    if not _table_exists(conn, "custom_form_definitions"):
        op.create_table(
            "custom_form_definitions",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("code", sa.String(20), nullable=False, unique=True),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("workflow_key", sa.String(50), nullable=False, server_default="cfm"),
            sa.Column("default_currency", sa.String(3), nullable=False, server_default="CAD"),
            sa.Column("field_schema", JSONB, nullable=False, server_default="[]"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    # ── Backfill from expense_policy_config.custom_forms (if the column exists) ──
    has_col = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='expense_policy_config' "
        "AND column_name='custom_forms'"
    )).first()
    if not has_col:
        return

    rows = conn.execute(sa.text("SELECT custom_forms FROM expense_policy_config LIMIT 1")).first()
    if not rows or not rows[0]:
        return

    forms = rows[0]
    if isinstance(forms, str):
        forms = json.loads(forms)

    for form in forms:
        code = (form.get("code") or "").strip().upper()
        if not code:
            continue
        exists = conn.execute(
            sa.text("SELECT 1 FROM custom_form_definitions WHERE code=:c"), {"c": code}
        ).first()
        if exists:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO custom_form_definitions "
                "(id, code, name, description, workflow_key, default_currency, field_schema, is_active) "
                "VALUES (gen_random_uuid(), :code, :name, :desc, :wf, :cur, CAST(:fs AS JSONB), :active)"
            ),
            {
                "code": code,
                "name": form.get("name") or code,
                "desc": form.get("description"),
                "wf": form.get("workflow_key") or f"cfm_{code.lower()}",
                "cur": form.get("default_currency") or "CAD",
                "fs": json.dumps(form.get("fields") or []),
                "active": bool(form.get("is_active", True)),
            },
        )


def downgrade() -> None:
    op.drop_table("custom_form_definitions")
