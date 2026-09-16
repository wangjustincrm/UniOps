"""Attach the month's sheet to the toolbox talk.

The topic *is* a one-page PDF — that is what a supervisor reads off and what
the crew signs against. The talk carried a title and a free-text topic and
nowhere to put the sheet, so the one artefact the meeting is built around had
to be emailed round separately, which is how a crew ends up signing for a
talk nobody can produce afterwards.

Same two columns as ehs_courses already uses for its material, so the file
plumbing is the one that exists rather than a second way of doing it.

Revision ID: 20260916_0027
Revises: 20260916_0026
Create Date: 2026-09-16
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260916_0027"
down_revision = "20260916_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_toolbox_talks",
                  sa.Column("material_file_id", UUID(as_uuid=True), nullable=True))
    op.add_column("ehs_toolbox_talks",
                  sa.Column("material_filename", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_toolbox_talks", "material_filename")
    op.drop_column("ehs_toolbox_talks", "material_file_id")
