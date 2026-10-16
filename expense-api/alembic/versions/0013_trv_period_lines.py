"""TRV period lines (days x daily rate, optional invoice cap) + trip country

Long overseas trips (2-3 months) cannot be claimed one day at a time. A TRV
line may now cover a date range: days claimed (<= days in the range) x a daily
rate, with an optional invoice amount the line total must not exceed. The
claim records the country travelled to, which decides whether lines carry
Canadian sales tax.

Revision ID: 0013_trv_period_lines
Revises: 0012_travel_application
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_trv_period_lines"
down_revision = "0012_travel_application"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("expense_line_items", sa.Column("period_from", sa.Date(), nullable=True))
    op.add_column("expense_line_items", sa.Column("period_to", sa.Date(), nullable=True))
    op.add_column("expense_line_items", sa.Column("period_days", sa.Integer(), nullable=True))
    op.add_column("expense_line_items", sa.Column("daily_rate", sa.Numeric(15, 2), nullable=True))
    op.add_column("expense_line_items", sa.Column("invoice_amount", sa.Numeric(15, 2), nullable=True))
    # NULL on every existing claim = Canada, which is what every TRV so far
    # has been taxed as (policy HST on each line).
    op.add_column("expense_claims", sa.Column("travel_country", sa.String(2), nullable=True))


def downgrade() -> None:
    op.drop_column("expense_claims", "travel_country")
    op.drop_column("expense_line_items", "invoice_amount")
    op.drop_column("expense_line_items", "daily_rate")
    op.drop_column("expense_line_items", "period_days")
    op.drop_column("expense_line_items", "period_to")
    op.drop_column("expense_line_items", "period_from")
