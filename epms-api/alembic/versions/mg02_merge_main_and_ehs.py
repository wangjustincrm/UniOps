"""Join the Safety branch's migrations to main's again.

Safety: mg01_merge_main_and_ehs (the merge point from 8 October).
main:   po01_po_payment_terms (everything since, Staff among it).

Both lines touch nothing in common, so this does no work of its own; it only
gives alembic one head. It was run on a copy of the dev3 database first.

Revision ID: mg02_merge_main_and_ehs
Revises: "mg01_merge_main_and_ehs", "po01_po_payment_terms"
"""
revision = "mg02_merge_main_and_ehs"
down_revision = ("mg01_merge_main_and_ehs", "po01_po_payment_terms")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
