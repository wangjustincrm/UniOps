"""aux_dimensions: string[] → [{code, required}] (per-dimension 必填/选填)

Each enabled auxiliary dimension now carries a `required` flag (default
false = optional). Existing string entries are migrated as optional;
finance marks the mandatory ones in the COA UI.

Revision ID: 0008_aux_required
Revises: 0007_purchase_expense
Create Date: 2026-06-12
"""
from alembic import op

revision = "0008_aux_required"
down_revision = "0007_purchase_expense"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        UPDATE chart_of_accounts
        SET aux_dimensions = COALESCE(
            (SELECT jsonb_agg(jsonb_build_object('code', el, 'required', false))
             FROM jsonb_array_elements_text(aux_dimensions) el),
            '[]'::jsonb)
        WHERE jsonb_typeof(aux_dimensions) = 'array'
          AND (aux_dimensions = '[]'::jsonb
               OR jsonb_typeof(aux_dimensions->0) = 'string')
    """)


def downgrade():
    op.execute("""
        UPDATE chart_of_accounts
        SET aux_dimensions = COALESCE(
            (SELECT jsonb_agg(el->>'code')
             FROM jsonb_array_elements(aux_dimensions) el),
            '[]'::jsonb)
        WHERE jsonb_typeof(aux_dimensions) = 'array'
          AND jsonb_typeof(aux_dimensions->0) = 'object'
    """)
