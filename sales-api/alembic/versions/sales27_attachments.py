"""sales27_attachments — a person attaches a file to an order or a transfer (user 2026-11-13): it is
kept as a document (kind ATTACHMENT, origin USER) linked to its record, like the EDI files. Only the
two check constraints learn the new values.

Revision id "sales27_attachments" is 19 characters (limit 32).
"""
from alembic import op

revision = 'sales27_attachments'
down_revision = 'sales26_stock_moves'
branch_labels = None
depends_on = None

OLD_KINDS = ("850_JSON", "940_EDI", "943_EDI", "944_EDI", "945_EDI", "997_EDI", "810_JSON", "INVOICE_EMAIL", "SHOPIFY_ORDER_JSON", "OTHER")
OLD_ORIGINS = ("PARTNER", "UNIOPS", "ZOHO_REFERENCE", "ZOHO_MIGRATION")


def _ck(name: str, column: str, values) -> None:
    op.drop_constraint(name, 'sales_documents', type_='check')
    op.create_check_constraint(name, 'sales_documents', f"{column} IN ({', '.join(repr(v) for v in values)})")


def upgrade() -> None:
    _ck('ck_sales_documents_kind', 'kind', OLD_KINDS + ("ATTACHMENT",))
    _ck('ck_sales_documents_origin', 'origin', OLD_ORIGINS + ("USER",))


def downgrade() -> None:
    _ck('ck_sales_documents_kind', 'kind', OLD_KINDS)
    _ck('ck_sales_documents_origin', 'origin', OLD_ORIGINS)
