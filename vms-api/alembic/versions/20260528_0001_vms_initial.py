"""VMS module — initial migration.

Creates all VMS-owned tables in the `public` schema with a `vms_` prefix.
Cross-table FKs reference `public.users` (the shared UniOps user master) —
the FK is real even though the `users` table is owned by epms-api.

Tables created:
  - vms_visitors
  - vms_visits
  - vms_badge_prints
  - vms_health_declarations
  - vms_audit_logs
  - vms_config (singleton, seeded with empty defaults)

Revision ID: 20260528_0001
Revises:
Create Date: 2026-05-28
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260528_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Enums ────────────────────────────────────────────────────────────────
    visitor_type = postgresql.ENUM(
        "supplier", "contractor", "inspector", "auditor", "customer",
        "interviewee", "other",
        name="vms_visitor_type",
        create_type=True,
    )
    visit_status = postgresql.ENUM(
        "pending_approval", "confirmed", "checked_in", "checked_out",
        "cancelled", "no_show",
        name="vms_visit_status",
        create_type=True,
    )
    access_area = postgresql.ENUM(
        "office", "warehouse", "production_non_gmp", "production_gmp",
        "laboratory", "all",
        name="vms_access_area",
        create_type=True,
    )
    visit_purpose = postgresql.ENUM(
        "meeting", "maintenance", "tour", "audit", "interview", "delivery",
        "other",
        name="vms_visit_purpose",
        create_type=True,
    )
    health_decl_status = postgresql.ENUM(
        "not_required", "passed", "failed", "restricted",
        name="vms_health_decl_status",
        create_type=True,
    )

    bind = op.get_bind()
    visitor_type.create(bind, checkfirst=True)
    visit_status.create(bind, checkfirst=True)
    access_area.create(bind, checkfirst=True)
    visit_purpose.create(bind, checkfirst=True)
    health_decl_status.create(bind, checkfirst=True)

    # ── vms_visitors ────────────────────────────────────────────────────────
    op.create_table(
        "vms_visitors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("job_title", sa.String(200), nullable=True),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("email", sa.String(200), nullable=True),
        sa.Column(
            "visitor_type",
            postgresql.ENUM(name="vms_visitor_type", create_type=False),
            nullable=False,
            server_default="other",
        ),
        sa.Column("id_verified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_vms_visitors_company_name", "vms_visitors", ["company_name"])
    op.create_index("ix_vms_visitors_email", "vms_visitors", ["email"])

    # ── vms_visits ──────────────────────────────────────────────────────────
    op.create_table(
        "vms_visits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "visitor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vms_visitors.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "host_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("visit_date", sa.Date, nullable=False),
        sa.Column("planned_arrival", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_departure", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_arrival", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_departure", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "visit_purpose",
            postgresql.ENUM(name="vms_visit_purpose", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "access_area",
            postgresql.ENUM(name="vms_access_area", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="vms_visit_status", create_type=False),
            nullable=False,
            server_default="confirmed",
        ),
        sa.Column(
            "health_decl_status",
            postgresql.ENUM(name="vms_health_decl_status", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "safety_training_confirmed",
            sa.Boolean, nullable=False, server_default=sa.false(),
        ),
        sa.Column("badge_returned", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("ppe_issued", postgresql.JSONB, nullable=True),
        sa.Column("accompanying_count", sa.Integer, nullable=True),
        sa.Column("vehicle_plate", sa.String(20), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        # Approval workflow fields (consumed by approval-api per PRD §6.2.1)
        sa.Column("approval_step_idx", sa.Integer, nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "quality_approver_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_vms_visits_visitor_id", "vms_visits", ["visitor_id"])
    op.create_index("ix_vms_visits_host_id", "vms_visits", ["host_id"])
    op.create_index("ix_vms_visits_visit_date", "vms_visits", ["visit_date"])
    op.create_index("ix_vms_visits_status", "vms_visits", ["status"])

    # ── vms_badge_prints ────────────────────────────────────────────────────
    op.create_table(
        "vms_badge_prints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "visit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vms_visits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "printed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "printed_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("reprint_reason", sa.String(500), nullable=True),
        sa.Column("template_used", sa.String(100), nullable=False, server_default="standard"),
    )
    op.create_index("ix_vms_badge_prints_visit_id", "vms_badge_prints", ["visit_id"])

    # ── vms_health_declarations ─────────────────────────────────────────────
    op.create_table(
        "vms_health_declarations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "visit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vms_visits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("questionnaire_data", postgresql.JSONB, nullable=False),
        sa.Column(
            "result",
            postgresql.ENUM(name="vms_health_decl_status", create_type=False),
            nullable=False,
        ),
        sa.Column("signature", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_vms_health_declarations_visit_id", "vms_health_declarations", ["visit_id"])

    # ── vms_audit_logs ──────────────────────────────────────────────────────
    # BIGINT autoincrement PK; immutability enforced in migration 0002.
    op.create_table(
        "vms_audit_logs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_name", sa.String(200), nullable=False),
        sa.Column("action_type", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("old_value", postgresql.JSONB, nullable=True),
        sa.Column("new_value", postgresql.JSONB, nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=False),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_index("ix_vms_audit_logs_timestamp", "vms_audit_logs", ["timestamp"])
    op.create_index("ix_vms_audit_logs_user_id", "vms_audit_logs", ["user_id"])
    op.create_index("ix_vms_audit_logs_action_type", "vms_audit_logs", ["action_type"])
    op.create_index("ix_vms_audit_logs_entity_type", "vms_audit_logs", ["entity_type"])
    op.create_index("ix_vms_audit_logs_entity_id", "vms_audit_logs", ["entity_id"])

    # ── vms_config (singleton) ──────────────────────────────────────────────
    op.create_table(
        "vms_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "quality_manager_user_ids",
            postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "notification_contacts",
            postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "health_questions",
            postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "badge_templates",
            postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Seed the singleton row immediately so vms-api code can rely on it.
    # Raw SQL via op.execute() so offline `--sql` mode also works (JSONB literals
    # have no literal-bind renderer in offline mode).
    op.execute(
        """
        INSERT INTO vms_config (
            id, quality_manager_user_ids, notification_contacts,
            health_questions, badge_templates
        ) VALUES (
            gen_random_uuid(), '[]'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb
        )
        """
    )


def downgrade() -> None:
    op.drop_table("vms_config")
    op.drop_table("vms_audit_logs")
    op.drop_table("vms_health_declarations")
    op.drop_table("vms_badge_prints")
    op.drop_table("vms_visits")
    op.drop_table("vms_visitors")

    bind = op.get_bind()
    for name in (
        "vms_health_decl_status", "vms_visit_purpose", "vms_access_area",
        "vms_visit_status", "vms_visitor_type",
    ):
        sa.Enum(name=name).drop(bind, checkfirst=True)
