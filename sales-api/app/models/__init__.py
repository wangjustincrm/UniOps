"""Import every model module here so alembic autogenerate and the test
schema see the tables (alembic/env.py imports this package)."""
from app.models import config  # noqa: F401,E402  — sales01_config
from app.models import records  # noqa: F401,E402  — sales02_audit_outbox
