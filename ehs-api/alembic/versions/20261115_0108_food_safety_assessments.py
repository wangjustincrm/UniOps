"""The Food Safety Risk Assessment (UAT 13).

Revision ID: 20261115_0108
Revises: 20261113_0107
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261115_0108"
down_revision = "20261113_0107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_food_safety_assessments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("incident_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ccp_failure", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("probability", sa.SmallInteger(), nullable=False),
        sa.Column("severity", sa.SmallInteger(), nullable=False),
        sa.Column("detectability", sa.SmallInteger(), nullable=False),
        sa.Column("rpn", sa.SmallInteger(), nullable=False),
        sa.Column("band", sa.String(10), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("assessed_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assessed_by_name", sa.String(255), nullable=True),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("probability between 1 and 5", name="ck_fsa_probability"),
        sa.CheckConstraint("severity between 1 and 5", name="ck_fsa_severity"),
        sa.CheckConstraint("detectability between 1 and 5", name="ck_fsa_detectability"),
    )
    op.create_index("ix_ehs_food_safety_assessments_incident_id",
                    "ehs_food_safety_assessments", ["incident_id"])
    op.create_index("ix_ehs_food_safety_assessments_band",
                    "ehs_food_safety_assessments", ["band"])


def downgrade() -> None:
    op.drop_table("ehs_food_safety_assessments")
