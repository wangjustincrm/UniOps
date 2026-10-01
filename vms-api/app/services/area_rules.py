"""What each access area requires — the one place these rules live.

Every rule that depends on the access area reads from here: approval routing
(services/approval.py), the badge-print health gate (crud/badge.py), the
training / PPE confirmation tasks (services/compliance.py), the GMP reports and
KPIs (services/reports.py), and — through GET /api/v1/area-rules — the VMS
frontend's hints and banners. They used to keep separate copies, which is how
Entire Plant ended up needing Quality Manager approval but no health
declaration (VMS PRD V2.7 §12 D-08).
"""
from app.models.visit import AccessArea

# Department Manager approval.
APPROVAL_AREAS = frozenset({
    AccessArea.warehouse,
    AccessArea.production_non_gmp,
    AccessArea.production_gmp,
    AccessArea.laboratory,
    AccessArea.all,
})

# GMP-grade areas. Entire Plant includes the GMP clean zone, so it is held to
# the same rules: Quality Manager approval, a passing health declaration for
# every visitor before the badge prints, training / PPE confirmation at
# check-in, and it counts in the GMP / Lab reports.
GMP_GRADE_AREAS = frozenset({
    AccessArea.production_gmp,
    AccessArea.laboratory,
    AccessArea.all,
})

QUALITY_MANAGER_AREAS = GMP_GRADE_AREAS
HEALTH_DECLARATION_AREAS = GMP_GRADE_AREAS
COMPLIANCE_TASK_AREAS = GMP_GRADE_AREAS


def rules_for(area: AccessArea) -> dict:
    return {
        "area": area.value,
        "requires_approval": area in APPROVAL_AREAS,
        "requires_quality_manager": area in QUALITY_MANAGER_AREAS,
        "requires_health_declaration": area in HEALTH_DECLARATION_AREAS,
        "compliance_tasks_at_check_in": area in COMPLIANCE_TASK_AREAS,
        "gmp_grade": area in GMP_GRADE_AREAS,
    }


def all_rules() -> list[dict]:
    return [rules_for(a) for a in AccessArea]
