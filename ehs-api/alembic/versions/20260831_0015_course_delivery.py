"""Running a course, not just recording that somebody took one.

The register (ehs_training_records) answers "is this person compliant". It
cannot answer "who is coming on Thursday", and HSE's process is built around
that question: create the session, attach the material, name a supervisor to
run its roster, remind the roster the day before, then mark each attendee
passed or failed. Passing writes the register row, which is why the enrolment
keeps a foreign key to it — the two must never disagree about whether the
certificate was issued.

`ehs_courses` gains `material_file_id` here as well: the standing handout for
a course belongs to the course, while a particular session may have its own.

Revision ID: 20260831_0015
Revises: 20260831_0014
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260831_0015"
down_revision = "20260831_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_courses", sa.Column("material_file_id", UUID(as_uuid=True), nullable=True))
    op.add_column("ehs_courses", sa.Column("material_filename", sa.String(255), nullable=True))

    op.create_table(
        "ehs_course_deliveries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("delivery_no", sa.String(20), nullable=False, unique=True),
        sa.Column("course_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_courses.id", ondelete="RESTRICT"), nullable=True, index=True),
        sa.Column("course_label", sa.String(200), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("session_date", sa.Date, nullable=False, index=True),
        sa.Column("start_time", sa.Time, nullable=True),
        sa.Column("end_time", sa.Time, nullable=True),
        sa.Column("location_text", sa.String(200), nullable=True),
        sa.Column("location_id", UUID(as_uuid=True), nullable=True),
        sa.Column("coordinator_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("coordinator_name", sa.String(255), nullable=True),
        sa.Column("instructor_name", sa.String(200), nullable=True),
        sa.Column("material_file_id", UUID(as_uuid=True), nullable=True),
        sa.Column("material_filename", sa.String(255), nullable=True),
        sa.Column("status", sa.String(15), nullable=False, server_default="scheduled",
                  index=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("reminded_on", sa.Date, nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    op.create_table(
        "ehs_course_enrolments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("delivery_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_course_deliveries.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("department_name", sa.String(255), nullable=True),
        sa.Column("result", sa.String(10), nullable=False, server_default="enrolled", index=True),
        sa.Column("marked_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("marked_by_name", sa.String(255), nullable=True),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("training_record_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_training_records.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("delivery_id", "user_id", name="uq_ehs_course_enrolment"),
    )


def downgrade() -> None:
    op.drop_table("ehs_course_enrolments")
    op.drop_table("ehs_course_deliveries")
    op.drop_column("ehs_courses", "material_filename")
    op.drop_column("ehs_courses", "material_file_id")
