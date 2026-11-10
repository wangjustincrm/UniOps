"""Expense attachments: which claim line a scanned receipt belongs to

OA ATT-OA-005: a receipt scanned on a line is that line's receipt, and
rescanning replaces the previously scanned file but never a manually attached
one. Attachments had no line and no origin, so a rescan on a later edit could
not find the earlier scan and the claim ended up with the receipt twice
(ISS-0197).

- line_item_id: the expense_line_items row the file was scanned/attached on.
  Deliberately no foreign key: update_claim deletes and re-inserts the lines
  (re-using the ids the client sends back), and ON DELETE would clear the link
  mid-save. NULL = not tied to a line (every existing attachment, uploads on
  the claim page).
- source: "scan" | "manual". NULL on existing rows = unknown, treated as
  manual, so nothing uploaded before this is ever replaced automatically.

Columns only; no data is changed.

Revision ID: 0014_attachment_line_source
Revises: 0013_trv_period_lines
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0014_attachment_line_source"
down_revision = "0013_trv_period_lines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("expense_attachments", sa.Column("line_item_id", UUID(as_uuid=True), nullable=True))
    op.add_column("expense_attachments", sa.Column("source", sa.String(10), nullable=True))
    op.create_index("ix_expense_attachments_line_item_id", "expense_attachments", ["line_item_id"])


def downgrade() -> None:
    op.drop_index("ix_expense_attachments_line_item_id", table_name="expense_attachments")
    op.drop_column("expense_attachments", "source")
    op.drop_column("expense_attachments", "line_item_id")
