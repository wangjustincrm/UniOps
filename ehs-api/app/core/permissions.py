"""Named permission dependencies for Safety endpoints.

Every key here must exist in identity's `permission_defs` (registered by
identity-api/alembic/versions/0013_ehs_perms.py) — `require_permission` has no
local fallback, so an unseeded key denies everyone except system_admin.
"""
from typing import Annotated

from fastapi import Depends

from app.core.authz import require_permission

# Reporting an incident is the one thing every employee can do, including
# production floor workers whose only reason to open Safety is to file one.
CanReportIncident = Annotated[dict, Depends(require_permission("ehs.incident.report"))]
CanReadIncident = Annotated[dict, Depends(require_permission("ehs.incident.read"))]
CanInvestigate = Annotated[dict, Depends(require_permission("ehs.incident.investigate"))]
CanCloseIncident = Annotated[dict, Depends(require_permission("ehs.incident.close"))]
# Classifying is what starts the statutory clocks, so it sits with
# investigation rather than with reporting.
CanClassifyIncident = Annotated[dict, Depends(require_permission("ehs.incident.investigate"))]

# Medical detail on an incident is PHIPA-sensitive and gated separately from
# the incident record itself.
CanReadMedical = Annotated[dict, Depends(require_permission("ehs.incident.medical.read"))]

CanReadAction = Annotated[dict, Depends(require_permission("ehs.action.read"))]
CanWriteAction = Annotated[dict, Depends(require_permission("ehs.action.write"))]
CanVerifyAction = Annotated[dict, Depends(require_permission("ehs.action.verify"))]

CanWriteFirstAid = Annotated[dict, Depends(require_permission("ehs.firstaid.write"))]

CanReadTraining = Annotated[dict, Depends(require_permission("ehs.training.read"))]
CanWriteTraining = Annotated[dict, Depends(require_permission("ehs.training.write"))]

CanReadWorker = Annotated[dict, Depends(require_permission("ehs.worker.read"))]
CanWriteWorker = Annotated[dict, Depends(require_permission("ehs.worker.write"))]

# Marking a statutory obligation as filed — the act that stops a legal clock.
CanManageStatutory = Annotated[dict, Depends(require_permission("ehs.statutory.manage"))]

# The Joint Health and Safety Committee: roster, meetings, recommendations.
CanReadJhsc = Annotated[dict, Depends(require_permission("ehs.jhsc.read"))]
CanManageJhsc = Annotated[dict, Depends(require_permission("ehs.jhsc.manage"))]

# Every active role holds ehs.document.read: Read & Sign asks people to
# acknowledge a policy, which they cannot do behind a 403.
CanReadDocument = Annotated[dict, Depends(require_permission("ehs.document.read"))]
CanManageDocument = Annotated[dict, Depends(require_permission("ehs.document.manage"))]

# Inspecting an extinguisher and editing the register are different acts by
# different people, so they are different keys.
CanReadAsset = Annotated[dict, Depends(require_permission("ehs.asset.read"))]
CanInspectAsset = Annotated[dict, Depends(require_permission("ehs.asset.inspect"))]
CanManageAsset = Annotated[dict, Depends(require_permission("ehs.asset.manage"))]

# Work permits. Three keys because three different people are involved: the
# person who needs the work done is not the person authorised to allow it, and
# conflating them is the defect the permit exists to prevent.
CanRequestPermit = Annotated[dict, Depends(require_permission("ehs.permit.request"))]
CanReadPermit = Annotated[dict, Depends(require_permission("ehs.permit.read"))]
CanIssuePermit = Annotated[dict, Depends(require_permission("ehs.permit.issue"))]

# Vocabularies, the plant area tree, escalation timings, notification templates.
CanManageSettings = Annotated[dict, Depends(require_permission("ehs.settings.manage"))]
