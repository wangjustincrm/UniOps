"""Safety equipment, and the inspection each item is due.

Extinguishers, AEDs, eyewash stations, safety showers, first-aid cabinets, gas
detectors, ladders, and the fall-arrest harnesses and lanyards HSE added in
place of relief valves.

Why this is not the form scheduler. That one answers "whose turn is the monthly
warehouse inspection" — it rotates departments and generates one run per
period. This one answers "when was extinguisher 47 last checked", which has to
be per asset or the register cannot be used for anything. Both open a task and
both can be answered with a checklist, but a rotation cannot express "asset 47
is three weeks overdue and its area currently has no working extinguisher".

`status = out_of_service` is the field that earns its keep: an extinguisher
that failed its check must stop counting as coverage for its area, and that is
a state on the asset, not a note on the last inspection.

Revision ID: 20260831_0018
Revises: 20260831_0017
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260831_0018"
down_revision = "20260831_0017"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "ehs_safety_assets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_no", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("asset_type_id", UUID(as_uuid=True), nullable=True),
        sa.Column("asset_type_label", sa.String(200), nullable=True),
        sa.Column("serial_no", sa.String(100), nullable=True),
        sa.Column("manufacturer", sa.String(150), nullable=True),
        sa.Column("model", sa.String(150), nullable=True),
        sa.Column("location_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("location_path", sa.String(500), nullable=True),
        sa.Column("department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("department_name", sa.String(255), nullable=True),
        sa.Column("inspection_frequency", sa.String(15), nullable=True),
        sa.Column("inspection_template_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_form_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assigned_role", sa.String(50), nullable=True),
        sa.Column("assigned_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("next_due_on", sa.Date, nullable=True, index=True),
        sa.Column("last_inspected_on", sa.Date, nullable=True),
        sa.Column("expires_on", sa.Date, nullable=True),
        sa.Column("status", sa.String(15), nullable=False, server_default="in_service",
                  index=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("qr_token", sa.String(64), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )

    op.create_table(
        "ehs_asset_inspections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_safety_assets.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("due_on", sa.Date, nullable=False, index=True),
        sa.Column("performed_on", sa.Date, nullable=True),
        sa.Column("performed_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("performed_by_name", sa.String(255), nullable=True),
        sa.Column("result", sa.String(10), nullable=False, server_default="due", index=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("submission_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_form_submissions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_actions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cycle_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        # The generator runs on every sweep; this is what makes a restart
        # mid-run safe rather than producing a second check for the same date.
        sa.UniqueConstraint("asset_id", "due_on", name="uq_ehs_asset_inspection_due"),
    )


def downgrade() -> None:
    op.drop_table("ehs_asset_inspections")
    op.drop_table("ehs_safety_assets")
