"""Invoice: separate column for the exception-resolution note

Revision ID: ex01_inv_exc_resolution_note
Revises: fr01_freight
Create Date: 2026-10-21

POST /invoices/{id}/exception used to write the AP clerk's justification into
exception_reason — the column match() owns and rewrites on every (re-)match.
Once accepted, the invoice is "matched", and nothing on the detail page showed
exception_reason any more; an edit with unchanged figures overwrote it with
"Variance accepted by ...". The note now has its own column.

Backfill: for resolved invoices, exception_reason still holds the note unless
match() has since rewritten it. The rewritten texts all start with one of a
fixed set of system prefixes (crud/invoice.py), so copy everything else.
exception_reason itself is left untouched.
"""
from alembic import op
import sqlalchemy as sa

revision = "ex01_inv_exc_resolution_note"
down_revision = "fr01_freight"
branch_labels = None
depends_on = None

_SYSTEM_PREFIXES = (
    "One or more PO lines are outside tolerance",
    "Partially invoiced against PO reference",
    "Auto-matched within tolerance",
    "Reviewed: variance outside tolerance",
    "Reviewed and approved",
    "Variance accepted by",
)


def upgrade() -> None:
    op.add_column("invoices", sa.Column("exception_resolution_note", sa.Text(), nullable=True))
    not_system = " AND ".join(
        f"exception_reason NOT LIKE '{p}%'" for p in _SYSTEM_PREFIXES)
    op.execute(f"""
        UPDATE invoices
           SET exception_resolution_note = exception_reason
         WHERE exception_resolution IS NOT NULL
           AND exception_reason IS NOT NULL
           AND btrim(exception_reason) <> ''
           AND {not_system}
    """)


def downgrade() -> None:
    op.drop_column("invoices", "exception_resolution_note")
