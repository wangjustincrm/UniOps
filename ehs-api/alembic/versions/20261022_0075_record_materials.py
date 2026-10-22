"""Several files on a training record.

Round 9, question 6: "allow for several files on a training record. all kinds
of file types." ehs_course_materials gains a third owner, the record.

Revision ID: 20261022_0075
Revises: 20261022_0074
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261022_0075"
down_revision = "20261022_0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_course_materials", sa.Column(
        "record_id", UUID(as_uuid=True),
        sa.ForeignKey("ehs_training_records.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_ehs_course_materials_record_id", "ehs_course_materials", ["record_id"])
    op.drop_constraint("ck_ehs_course_material_owner", "ehs_course_materials", type_="check")
    op.create_check_constraint(
        "ck_ehs_course_material_owner", "ehs_course_materials",
        "num_nonnulls(course_id, delivery_id, record_id) = 1")


def downgrade() -> None:
    op.execute("DELETE FROM ehs_course_materials WHERE record_id IS NOT NULL")
    op.drop_constraint("ck_ehs_course_material_owner", "ehs_course_materials", type_="check")
    op.create_check_constraint(
        "ck_ehs_course_material_owner", "ehs_course_materials",
        "num_nonnulls(course_id, delivery_id) = 1")
    op.drop_index("ix_ehs_course_materials_record_id", table_name="ehs_course_materials")
    op.drop_column("ehs_course_materials", "record_id")
