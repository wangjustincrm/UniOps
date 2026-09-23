"""A course can hold more than one piece of material.

One file per course was the shape, and a course is rarely one file: the deck,
the handout, the sign-off sheet, last year's revision. HSE asked for more than
one plainly. The single columns stay and are backfilled into the new table as
the first item, so nothing that already attached material loses it.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260922_0032" is 13 characters.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260922_0032"
down_revision = "20260922_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_course_materials",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("course_id", sa.UUID(), nullable=False),
        sa.Column("file_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uploaded_by", sa.UUID(), nullable=True),
        sa.Column("uploaded_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["ehs_courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_ehs_course_materials_course_id",
                    "ehs_course_materials", ["course_id"])

    # Whatever is already attached becomes the first item. The size and type are
    # not recorded on the course row, so they are left at their defaults rather
    # than guessed — a wrong content type would make the file undownloadable.
    op.execute("""
        INSERT INTO ehs_course_materials
              (id, course_id, file_id, filename, content_type, file_size, created_at)
        SELECT gen_random_uuid(), id, material_file_id,
               coalesce(material_filename, 'course material'),
               'application/octet-stream', 0, now()
          FROM ehs_courses
         WHERE material_file_id IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_ehs_course_materials_course_id", table_name="ehs_course_materials")
    op.drop_table("ehs_course_materials")
