"""The hazard register and job safety analyses.

Two records that look alike and are not, which is why they are two tables.

A hazard is a standing fact about the plant — the pinch point on the case
sealer is there whether or not anyone is working today. It is owned, reviewed,
and either controlled or not. The register is what an auditor reads to see that
the employer knows what is dangerous about its own site.

A JSA is one job broken into steps, each step with its hazards and its
controls, signed by the crew before they start. Versioned, because a crew
signing last year's method statement for this year's work is the failure the
whole document exists to prevent — and signed per person per working day,
because the same JSA is signed afresh every time the job runs.

`ehs_jsa_steps` gives hazards and controls their own columns rather than one
method-statement paragraph. A step with a hazard and no control is the row
worth finding, and a paragraph hides it.

Revision ID: 20260831_0019
Revises: 20260831_0018
Create Date: 2026-09-01
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260831_0019"
down_revision = "20260831_0018"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "ehs_hazards",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("hazard_no", sa.String(20), nullable=False, unique=True),
        sa.Column("title", sa.String(250), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("hazard_type_id", UUID(as_uuid=True), nullable=True),
        sa.Column("hazard_type_label", sa.String(200), nullable=True),
        sa.Column("location_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("location_path", sa.String(500), nullable=True),
        sa.Column("department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("department_name", sa.String(255), nullable=True),
        sa.Column("equipment_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_equipment.id", ondelete="SET NULL"), nullable=True),
        sa.Column("risk_assessment_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_risk_assessments.id", ondelete="SET NULL"), nullable=True),
        # Denormalised from the assessment so the register can be sorted by
        # risk without a join per line — a ranked list is its whole use.
        sa.Column("current_rating", sa.String(20), nullable=True, index=True),
        sa.Column("residual_rating", sa.String(20), nullable=True),
        sa.Column("existing_controls", sa.Text, nullable=True),
        sa.Column("control_level", sa.String(20), nullable=True),
        sa.Column("owner_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("owner_name", sa.String(255), nullable=True),
        sa.Column("identified_on", sa.Date, nullable=True),
        sa.Column("review_due_on", sa.Date, nullable=True, index=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="open", index=True),
        sa.Column("source_type", sa.String(20), nullable=True),
        sa.Column("source_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )

    op.create_table(
        "ehs_jsa",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("jsa_no", sa.String(20), nullable=False, unique=True),
        sa.Column("title", sa.String(250), nullable=False),
        sa.Column("job_description", sa.Text, nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("location_id", UUID(as_uuid=True), nullable=True),
        sa.Column("location_path", sa.String(500), nullable=True),
        sa.Column("department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("department_name", sa.String(255), nullable=True),
        sa.Column("equipment_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_equipment.id", ondelete="SET NULL"), nullable=True),
        sa.Column("required_ppe", sa.Text, nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft", index=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by_name", sa.String(255), nullable=True),
        sa.Column("review_due_on", sa.Date, nullable=True),
        sa.Column("supersedes_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_jsa.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )

    op.create_table(
        "ehs_jsa_steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("jsa_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_jsa.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("seq", sa.Integer, nullable=False, server_default="1"),
        sa.Column("step", sa.Text, nullable=False),
        sa.Column("hazards", sa.Text, nullable=True),
        sa.Column("controls", sa.Text, nullable=True),
        sa.Column("control_level", sa.String(20), nullable=True),
        sa.Column("hazard_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_hazards.id", ondelete="SET NULL"), nullable=True),
        sa.Column("residual_rating", sa.String(20), nullable=True),
    )

    op.create_table(
        "ehs_jsa_signoffs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("jsa_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_jsa.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("person_name", sa.String(255), nullable=False),
        sa.Column("person_role", sa.String(150), nullable=True),
        sa.Column("signature", sa.Text, nullable=True),
        sa.Column("worked_on", sa.Date, nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("is_supervisor", sa.Boolean, nullable=False, server_default="false"),
        # The same JSA is signed afresh each day the job runs, so the day is
        # part of the key rather than something to deduplicate away.
        sa.UniqueConstraint("jsa_id", "user_id", "worked_on", name="uq_ehs_jsa_signoff"),
    )


def downgrade() -> None:
    op.drop_table("ehs_jsa_signoffs")
    op.drop_table("ehs_jsa_steps")
    op.drop_table("ehs_jsa")
    op.drop_table("ehs_hazards")
