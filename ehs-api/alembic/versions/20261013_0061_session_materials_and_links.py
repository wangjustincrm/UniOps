"""Material belongs to a course or to one session, and may be a link.

Round 6, TC-06: "When the training session is created, if there are
attachments attached in the training course, transfer a copy over to the
session. add the ability to add a url and a video, all file types would be
good."

One table for both, so a session's copy is the same kind of row as the course's
original: delivery_id for a session's, course_id for a course's, never both.
A row is a file or a link, never both. The session's old single file
(material_file_id) is brought in as its first item, the same way the course's
was when this table was made.

Revision ID: 20261013_0061
Revises: 20261013_0060
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261013_0061"
down_revision = "20261013_0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_course_materials", sa.Column(
        "delivery_id", UUID(as_uuid=True),
        sa.ForeignKey("ehs_course_deliveries.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_ehs_course_materials_delivery_id", "ehs_course_materials", ["delivery_id"])
    op.add_column("ehs_course_materials", sa.Column("url", sa.String(2000), nullable=True))
    op.alter_column("ehs_course_materials", "course_id", nullable=True)
    op.alter_column("ehs_course_materials", "file_id", nullable=True)
    op.create_check_constraint(
        "ck_ehs_course_material_owner", "ehs_course_materials",
        "num_nonnulls(course_id, delivery_id) = 1")
    op.create_check_constraint(
        "ck_ehs_course_material_kind", "ehs_course_materials",
        "num_nonnulls(file_id, url) = 1")
    op.execute("""
        INSERT INTO ehs_course_materials
               (id, delivery_id, file_id, filename, content_type, file_size,
                uploaded_by, uploaded_by_name, created_at)
        SELECT gen_random_uuid(), d.id, d.material_file_id,
               coalesce(d.material_filename, 'material'), 'application/octet-stream', 0,
               d.created_by, d.created_by_name, d.created_at
          FROM ehs_course_deliveries d
         WHERE d.material_file_id IS NOT NULL
    """)


def downgrade() -> None:
    # Session rows and links have nowhere to go in the old shape.
    op.execute("DELETE FROM ehs_course_materials WHERE delivery_id IS NOT NULL OR url IS NOT NULL")
    op.drop_constraint("ck_ehs_course_material_kind", "ehs_course_materials", type_="check")
    op.drop_constraint("ck_ehs_course_material_owner", "ehs_course_materials", type_="check")
    op.alter_column("ehs_course_materials", "file_id", nullable=False)
    op.alter_column("ehs_course_materials", "course_id", nullable=False)
    op.drop_column("ehs_course_materials", "url")
    op.drop_index("ix_ehs_course_materials_delivery_id", table_name="ehs_course_materials")
    op.drop_column("ehs_course_materials", "delivery_id")
