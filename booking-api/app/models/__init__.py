"""Register all ORM models so that Base.metadata is populated before
alembic autogenerate or Base.metadata.create_all() is called.

Import order respects FK dependencies:
  user_mirror (no deps) → room (no deps) → booking (→ room) →
  notification / audit (→ booking) → booking_config (no deps)
"""
from app.models.user_mirror import User  # noqa: F401
from app.models.room import MeetingRoom  # noqa: F401
from app.models.booking import Booking  # noqa: F401
from app.models.notification import NotificationLog  # noqa: F401
from app.models.audit import BookingAuditLog  # noqa: F401
from app.models.booking_config import BookingConfig  # noqa: F401
