"""Workplace labels for chemical containers, and the approval gate.

WHMIS requires a workplace label on any container a product is decanted into.
The information all sits on the supplier's data sheet; the work is transcribing
it, and transcribing it wrongly is a violation rather than a typo — a container
labelled with the wrong pictogram is worse than one with none, because somebody
will believe it.

HSE's answer to question 6.8 was unambiguous: "HSE approval required before any
label can be printed." So `status` moves draft → approved → printed and the PDF
endpoint refuses anything not approved. Extraction from the data sheet fills the
draft in; it never publishes.

`extraction` keeps what the model returned, verbatim, with its own per-field
confidence. A reviewer can then see what was suggested against what was
approved, and a bad extraction is diagnosable rather than deniable.

Revision ID: 20260902_0025
Revises: 20260902_0024
Create Date: 2026-09-02
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260902_0025"
down_revision = "20260902_0024"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "ehs_chemical_labels",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("chemical_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_chemicals.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("sds_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_sds.id", ondelete="SET NULL"), nullable=True),
        sa.Column("product_identifier", sa.String(250), nullable=False),
        sa.Column("supplier", sa.String(200), nullable=True),
        sa.Column("signal_word", sa.String(10), nullable=True),
        sa.Column("hazard_statements", JSONB, nullable=False, server_default="[]"),
        sa.Column("precautionary_statements", JSONB, nullable=False, server_default="[]"),
        sa.Column("pictograms", JSONB, nullable=False, server_default="[]"),
        sa.Column("first_aid", sa.Text, nullable=True),
        sa.Column("ppe_note", sa.Text, nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("extraction", JSONB, nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="draft", index=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by_name", sa.String(255), nullable=True),
        sa.Column("print_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_printed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )


def downgrade() -> None:
    op.drop_table("ehs_chemical_labels")
