"""WMS quality-status -> internal availability-status mapping (config, not code).

Seeded by migration mrp01 from the QLT_STS dictionary surveyed live against
Flux WMS (design doc appendix A): 01=Block, 02=Release, 04=Under Inspection.
Kept as a DB table (not hardcoded in the sync service) so ops can retune the
mapping — or add a currently-unobserved QLT_STS code — without a deploy.
`expired` is never a row here: it is always derived at transform time from
`expiry_date < today`, overriding whatever this table says.
"""
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class MrpStatusMapping(Base, TimestampMixin):
    __tablename__ = "mrp_status_mapping"

    wms_code: Mapped[str] = mapped_column(String(10), primary_key=True)  # <- QLT_STS.BSM_CODE ('01'/'02'/'04'/...)
    mapped_status: Mapped[str] = mapped_column(String(20), nullable=False)  # available|hold
    description: Mapped[str | None] = mapped_column(String(100))
