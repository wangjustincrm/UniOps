"""Assembling the WSIB Form 7 data package.

Everything here is read-only assembly: it gathers what the incident, the
injured person, the worker profile and the return-to-work plan already hold,
and reports what is absent. Nothing about it submits anything — see
app/schemas/wsib.py for why that boundary exists.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import Holiday
from app.models.incident import Incident, IncidentPerson
from app.models.mirrors import Department, User
from app.models.rtw import RtwPlan
from app.models.statutory import StatutoryDeadline
from app.models.worker import WorkerProfile
from app.schemas.wsib import Form7Package, MissingField
from app.services import statutory

# Fields WSIB asks for that this system may not hold, with the reason each one
# matters — a bare list of names would leave whoever files it guessing.
_REQUIRED = {
    "worker_name": "WSIB cannot match the claim to a worker without it",
    "occurred_at": "the date and time of injury is a mandatory field",
    "employer_aware_at": "the three-business-day clock is measured from this date",
    "body_part": "WSIB codes the claim by part of body",
    "nature_of_injury": "WSIB codes the claim by type of injury",
    "description": "the form asks how the injury happened, in the employer's words",
}


async def build_package(db: AsyncSession, incident: Incident) -> Form7Package:
    injured = (await db.execute(
        select(IncidentPerson).where(
            IncidentPerson.incident_id == incident.id,
            IncidentPerson.role == "injured",
        ).order_by(IncidentPerson.created_at)
    )).scalars().first()

    profile = None
    department_name = None
    hire_date = None
    if injured is not None and injured.user_id is not None:
        profile = (await db.execute(
            select(WorkerProfile).where(WorkerProfile.user_id == injured.user_id)
        )).scalar_one_or_none()
        if profile is not None:
            hire_date = profile.hire_date
        dept_id = injured.department_id or (await db.execute(
            select(User.department_id).where(User.id == injured.user_id)
        )).scalar()
        if dept_id:
            department_name = (await db.execute(
                select(Department.name).where(Department.id == dept_id)
            )).scalar()

    rtw = (await db.execute(
        select(RtwPlan).where(RtwPlan.incident_id == incident.id)
        .order_by(RtwPlan.start_date)
    )).scalars().first()

    deadline = (await db.execute(
        select(StatutoryDeadline).where(
            StatutoryDeadline.source_type == "incident",
            StatutoryDeadline.source_id == incident.id,
            StatutoryDeadline.kind == "wsib_form7",
        )
    )).scalar_one_or_none()

    internal = None
    if deadline is not None:
        holidays = frozenset((await db.execute(select(Holiday.holiday_date))).scalars().all())
        internal = statutory.internal_target("wsib_form7", deadline.due_at, holidays)

    package = Form7Package(
        incident_id=incident.id,
        incident_no=incident.incident_no,
        generated_at=datetime.now(timezone.utc),
        employer_name="Canada Royal Milk",
        employer_address="1680 Venture Dr, Kingston, Ontario",
        worker_name=injured.person_name if injured else None,
        worker_employee_no=profile.employee_no if profile else None,
        worker_department=department_name,
        worker_hire_date=hire_date,
        worker_is_employee=bool(injured and injured.user_id),
        worker_external_company=injured.external_company if injured else None,
        occurred_at=incident.occurred_at,
        employer_aware_at=incident.employer_aware_at,
        location_path=incident.location_path,
        body_part=injured.body_part_label if injured else None,
        nature_of_injury=injured.nature_of_injury_label if injured else None,
        injury_class=incident.injury_class,
        description=incident.description,
        witnesses=incident.witnesses_note,
        treatment=injured.treatment if injured else None,
        sent_offsite=None,
        modified_duties=rtw.modified_duties if rtw else None,
        at_regular_pay=rtw.at_regular_pay if rtw else None,
        rtw_start_date=rtw.start_date if rtw else None,
        due_at=deadline.due_at if deadline else None,
        internal_target=internal,
        filed_at=deadline.satisfied_at if deadline else None,
        confirmation_number=deadline.evidence_note if deadline else None,
    )

    values = package.model_dump()
    package.missing = [
        MissingField(field=name, why_it_matters=why)
        for name, why in _REQUIRED.items()
        if not values.get(name)
    ]
    package.is_complete = not package.missing
    return package
