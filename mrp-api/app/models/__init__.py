"""Import all models so Alembic + SQLAlchemy can register them."""
from app.models.wms_inventory import WmsInventoryLot  # noqa: F401
from app.models.status_mapping import MrpStatusMapping  # noqa: F401
from app.models.sync_state import MrpSyncState  # noqa: F401
from app.models.forecast import ForecastVersion, ForecastLine  # noqa: F401
from app.models.consignment import ConsignmentStock  # noqa: F401
