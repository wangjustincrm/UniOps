"""sales07_user_views — each user's saved views of the lists (design §13.19).

A view is a named, versioned ViewState (columns, order, hidden, sort, group,
filters, search) of one list (`resource`), owned by one user. A name is
unique per user and list whatever its case; at most one view per user and
list is the default (partial unique index).

Revision id "sales07_user_views" is 18 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'sales07_user_views'
down_revision = 'sales06_unit_shopify_titles'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'sales_user_views',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('resource', sa.String(length=100), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('state', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('is_default', sa.Boolean(), nullable=False),
        sa.Column('sort', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_by_name', sa.String(length=255), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_name', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('uq_sales_user_views_name', 'sales_user_views',
                    ['user_id', 'resource', sa.text('lower(name)')], unique=True)
    op.create_index('uq_sales_user_views_default', 'sales_user_views', ['user_id', 'resource'], unique=True,
                    postgresql_where=sa.text('is_default'))


def downgrade() -> None:
    op.drop_index('uq_sales_user_views_default', table_name='sales_user_views')
    op.drop_index('uq_sales_user_views_name', table_name='sales_user_views')
    op.drop_table('sales_user_views')
