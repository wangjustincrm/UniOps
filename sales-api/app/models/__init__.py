"""Import every model module here so alembic autogenerate and the test
schema see the tables (alembic/env.py imports this package)."""
from app.models import config  # noqa: F401,E402  — sales01_config
from app.models import records  # noqa: F401,E402  — sales02_audit_outbox
from app.models import documents  # noqa: F401,E402  — sales03_documents_runs
from app.models import orders  # noqa: F401,E402  — sales05_orders
from app.models import shadow  # noqa: F401,E402  — sales09_shadow
from app.models import shipments  # noqa: F401,E402  — sales10_shipments
from app.models import invoices  # noqa: F401,E402  — sales11_invoicing
from app.models import transfers  # noqa: F401,E402  — sales12_transfers
from app.models import lots  # noqa: F401,E402  — a read model (no table)
from app.models import payments  # noqa: F401,E402  — sales17_shopify_finance
