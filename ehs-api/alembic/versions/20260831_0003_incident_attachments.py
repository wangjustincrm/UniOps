"""Photographs taken at the scene, which until now went nowhere.

The report form has captured and client-side-compressed photos since launch,
but there was no upload step: they lived in the browser draft and were dropped
on submit. For an injury report that is the worst attachment to lose — the
scene changes within minutes of anyone looking at it, and a photograph is
frequently the only record of what the guard, the spill or the housekeeping
actually looked like.

Bytes go to file-api like every other attachment in UniOps; this table holds
the join, following the same shape as `pr_attachments` (filename, content type,
size, and the storage key file-api returned). file-api has no "list by
document" endpoint, so the owning service is what remembers which files belong
to which record.

`uploaded_by` is nullable with ON DELETE SET NULL, and the name is snapshotted,
per the module's rule for every users FK.

Revision ID: 20260831_0003
Revises: 20260829_0002
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260831_0003"
down_revision = "20260829_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_incident_attachments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("incident_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("storage_key", UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.Integer, nullable=False),
        sa.Column("uploaded_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ehs_incident_attachments_incident_id",
                    "ehs_incident_attachments", ["incident_id"])


def downgrade() -> None:
    op.drop_index("ix_ehs_incident_attachments_incident_id",
                  table_name="ehs_incident_attachments")
    op.drop_table("ehs_incident_attachments")
