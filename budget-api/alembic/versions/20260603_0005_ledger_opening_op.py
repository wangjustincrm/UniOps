"""Add 'opening' to budget_ledger operation check constraint.

Enables manual import of 期初 (opening balances) for Budget Actual. An 'opening'
row counts toward actual_spent (consumes budget), same bucket as actualize /
book_expense, but is sourced from a manual CSV import rather than a cross-service
write. See crud/opening.py and the Budget Config import UI.

Revision ID: 20260603_0005
Revises: 20260527_0004
Create Date: 2026-06-03
"""
from alembic import op


revision = "20260603_0005"
down_revision = "20260527_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_ledger_op", "budget_ledger", type_="check")
    op.create_check_constraint(
        "ck_ledger_op",
        "budget_ledger",
        "operation IN ('commit','release','actualize','book_expense','opening')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ledger_op", "budget_ledger", type_="check")
    op.create_check_constraint(
        "ck_ledger_op",
        "budget_ledger",
        "operation IN ('commit','release','actualize','book_expense')",
    )
