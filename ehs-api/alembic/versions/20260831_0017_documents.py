"""The policy library, and the record of who has read what.

Two obligations, one table pair. OHSA s.25(2)(j) requires the health and safety
policy to be reviewed at least annually — `review_due_on` puts that review into
ehs_statutory_deadlines alongside every other clock. And Read & Sign exists
because a policy nobody has read is not a control; the useful half of that
feature is the list of people who have *not* signed, so acknowledgement has to
be a row per person per version rather than a flag on the document.

`version` is repeated on the acknowledgement deliberately. Someone who signed
v2 is outstanding on v3, and storing only a document id would quietly report
them as done.

`qr_token` is unique and indexed: it is the lookup key when somebody points a
phone at the label on a shelf.

Revision ID: 20260831_0017
Revises: 20260831_0016
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260831_0017"
down_revision = "20260831_0016"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "ehs_documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("doc_no", sa.String(20), nullable=False, unique=True),
        sa.Column("title", sa.String(250), nullable=False),
        sa.Column("category", sa.String(20), nullable=False, server_default="policy", index=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("version", sa.String(20), nullable=False, server_default="1.0"),
        sa.Column("file_id", UUID(as_uuid=True), nullable=True),
        sa.Column("filename", sa.String(255), nullable=True),
        sa.Column("effective_on", sa.Date, nullable=True),
        sa.Column("review_due_on", sa.Date, nullable=True, index=True),
        sa.Column("review_months", sa.Integer, nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft", index=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("published_by_name", sa.String(255), nullable=True),
        sa.Column("supersedes_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("requires_ack", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("ack_roles", JSONB, nullable=False, server_default="[]"),
        sa.Column("qr_token", sa.String(64), nullable=True, unique=True),
        sa.Column("owner_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("owner_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )

    op.create_table(
        "ehs_document_acks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_documents.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("user_role", sa.String(150), nullable=True),
        sa.Column("signature", sa.Text, nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), **_TS),
        sa.UniqueConstraint("document_id", "user_id", "version", name="uq_ehs_document_ack"),
    )


def downgrade() -> None:
    op.drop_table("ehs_document_acks")
    op.drop_table("ehs_documents")
