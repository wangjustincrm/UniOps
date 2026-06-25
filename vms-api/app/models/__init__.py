"""Import all models so Alembic + SQLAlchemy can register them.

Read-only mirrors (see user_mirror.py) are registered too — vms-api does not
own them and its Alembic migrations skip them; they must already exist in
the shared DB.
"""
from app.models.visitor import Visitor, VisitorType  # noqa: F401
from app.models.visit import (  # noqa: F401
    Visit,
    VisitStatus,
    AccessArea,
    VisitPurpose,
    HealthDeclStatus,
)
from app.models.badge_print import BadgePrint  # noqa: F401
from app.models.health_declaration import HealthDeclaration  # noqa: F401
from app.models.audit_log import AuditLog  # noqa: F401
from app.models.vms_config import VmsConfig  # noqa: F401
from app.models.admin_audit_log import AdminAuditLog  # noqa: F401

# ── Read-only mirrors (other services own these tables) ────────────────────-
from app.models.user_mirror import User  # noqa: F401
from app.models.department_mirror import Department  # noqa: F401  # epms-api
from app.models.company_config_mirror import CompanyConfig  # noqa: F401  # epms-api
from app.models.file_metadata_mirror import FileMetadata  # noqa: F401  # file-api
from app.models.task_mirror import Task  # noqa: F401  # epms-api
