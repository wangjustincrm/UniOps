"""Reference data the module cannot start empty.

Three kinds of seed, for three different reasons.

**Vocabularies.** The list *definitions* are created here because code refers
to them by code — `root_cause`, `body_part` — but their *entries* are mostly
left empty on purpose. Those are the HSE Manager's to supply and maintain
(PRD v0.3 question 6.10); seeding guesses would mean someone silently
investigating against invented categories. Two vocabularies are the exception
and ship complete: the hierarchy of controls is an international standard, and
the injury classes drive statutory reporting and the injury-rate calculations.
Both are marked system-locked so entries cannot be added or removed.

**Ontario statutory holidays, 2026-2030.** The WSIB clock is three *business*
days, so it has to skip these, and Postgres has no idea what a holiday is.
Generated rather than typed out: the four Monday-based holidays follow a rule,
and only Easter needs a table. **This list expires at the end of 2030** - when
it lapses, WSIB deadlines quietly compute a day early, which is the kind of
failure nobody notices until an audit. Topping it up is an annual operations
task.

**Statutory courses.** The eight the HSE Manager confirmed on 2026-08-28. Four
apply to every employee - note that Working at Heights is among them, which is
broader than the common practice of restricting it to those working at height,
and was confirmed as intentional.

Revision id: alembic_version_ehs.version_num is varchar(32);
"20260829_0002" is 13 characters.
"""
import uuid
from datetime import date, timedelta

import sqlalchemy as sa
from alembic import op

revision = "20260829_0002"
down_revision = "20260829_0001"
branch_labels = None
depends_on = None

_NS = uuid.UUID("2c9e7a51-63d4-4f8b-91ac-7e5d3b016f92")


def _id(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))


# ── Vocabularies ────────────────────────────────────────────────────────────
# (code, name, hierarchical, system_locked, description)
_VOCABS = [
    ("incident_category", "Incident Category", False, False,
     "Injury, near miss, property damage, environmental release, occupational illness, workplace violence."),
    ("immediate_cause", "Immediate Cause", False, False,
     "What directly brought the incident about. Supplied by HSE."),
    ("root_cause", "Root Cause", True, False,
     "Grouped underlying causes. Grouping is what makes recurring-cause analysis possible."),
    ("hazard", "Hazard", False, False, "Hazards identified during investigation or inspection."),
    ("ppe", "PPE Type", False, False, "Personal protective equipment."),
    ("shift", "Shift", False, False, "Shift patterns - a reporting dimension, not a schedule."),
    ("position", "Position", False, False, "Job titles that drive training requirements."),
    ("body_part", "Body Part", False, False, "WSIB Form 7 field; align wording with WSIB."),
    ("nature_of_injury", "Nature of Injury", False, False, "WSIB Form 7 field."),
    ("cert_type", "Certification Type", False, False, "Licences and certificates held by workers."),
    ("safety_asset_type", "Safety Equipment Type", False, False,
     "Extinguishers, AEDs, eyewash stations, fall arrest equipment."),
    ("contractor_doc_type", "Contractor Document Type", False, False,
     "Certificate of insurance, WSIB clearance certificate."),
    ("hierarchy_of_control", "Hierarchy of Controls", False, True,
     "International standard. Locked - entries cannot be added or removed."),
    ("injury_class", "Injury Classification", False, True,
     "Drives statutory reporting and the injury rates. Locked: adding a level would make "
     "this year's rates incomparable with last year's."),
]

_LOCKED_ITEMS = {
    "hierarchy_of_control": [
        ("elimination", "Elimination", 10),
        ("substitution", "Substitution", 20),
        ("engineering", "Engineering Control", 30),
        ("administrative", "Administrative Control", 40),
        ("ppe", "Personal Protective Equipment", 50),
    ],
    # Wording taken from the HSE Manager's own Medical Incident form.
    "injury_class": [
        ("first_aid", "First Aid Injury", 10),
        ("medical_aid", "Medical Aid", 20),
        ("lost_time", "Lost Time", 30),
    ],
}

# ── Statutory courses (HSE Manager, 2026-08-28) ─────────────────────────────
# (code, name, applies_to_all, validity_months)
_COURSES = [
    ("WHMIS", "WHMIS", True, 12),
    ("LOTO", "Lockout Tagout", True, 36),
    ("AWARENESS", "Worker and Supervisor Safety Awareness", True, None),
    ("HEIGHTS", "Working at Heights", True, 36),
    ("FORKLIFT", "Forklift", False, 36),
    ("MEWP", "Mobile Equipment Work Platform", False, 36),
    ("CONFINED", "Confined Space", False, 36),
    ("FIRSTAID", "First Aid", False, 36),
]

# Easter Sunday, needed only for Good Friday. Computus is not worth carrying
# for a five-year window.
_EASTER = {
    2026: date(2026, 4, 5), 2027: date(2027, 3, 28), 2028: date(2028, 4, 16),
    2029: date(2029, 4, 1), 2030: date(2030, 4, 21),
}


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The nth given weekday of a month (weekday 0 = Monday)."""
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _victoria_day(year: int) -> date:
    """The Monday on or before 24 May."""
    d = date(year, 5, 24)
    return d - timedelta(days=(d.weekday() - 0) % 7)


def _ontario_holidays(year: int) -> list[tuple[date, str]]:
    return [
        (date(year, 1, 1), "New Year's Day"),
        (_nth_weekday(year, 2, 0, 3), "Family Day"),
        (_EASTER[year] - timedelta(days=2), "Good Friday"),
        (_victoria_day(year), "Victoria Day"),
        (date(year, 7, 1), "Canada Day"),
        (_nth_weekday(year, 9, 0, 1), "Labour Day"),
        (_nth_weekday(year, 10, 0, 2), "Thanksgiving"),
        (date(year, 12, 25), "Christmas Day"),
        (date(year, 12, 26), "Boxing Day"),
    ]


SEED_YEARS = range(2026, 2031)


def upgrade() -> None:
    vocab_t = sa.table(
        "ehs_vocabularies",
        sa.column("code", sa.String()), sa.column("name", sa.String()),
        sa.column("description", sa.Text()), sa.column("is_hierarchical", sa.Boolean()),
        sa.column("is_system_locked", sa.Boolean()),
    )
    op.bulk_insert(vocab_t, [
        {"code": c, "name": n, "description": d, "is_hierarchical": h, "is_system_locked": lk}
        for c, n, h, lk, d in _VOCABS
    ])

    item_t = sa.table(
        "ehs_vocabulary_items",
        sa.column("id", sa.UUID()), sa.column("vocabulary_code", sa.String()),
        sa.column("code", sa.String()), sa.column("label", sa.String()),
        sa.column("sort_order", sa.Integer()), sa.column("is_active", sa.Boolean()),
        sa.column("path", sa.String()),
    )
    items = []
    for vocab, entries in _LOCKED_ITEMS.items():
        for code, label, sort in entries:
            items.append({
                "id": _id("item", vocab, code), "vocabulary_code": vocab, "code": code,
                "label": label, "sort_order": sort, "is_active": True, "path": label,
            })
    op.bulk_insert(item_t, items)

    holiday_t = sa.table(
        "ehs_holidays",
        sa.column("id", sa.UUID()), sa.column("year", sa.SmallInteger()),
        sa.column("holiday_date", sa.Date()), sa.column("name", sa.String()),
    )
    op.bulk_insert(holiday_t, [
        {"id": _id("holiday", str(d)), "year": y, "holiday_date": d, "name": name}
        for y in SEED_YEARS for d, name in _ontario_holidays(y)
    ])

    course_t = sa.table(
        "ehs_courses",
        sa.column("id", sa.UUID()), sa.column("code", sa.String()),
        sa.column("name", sa.String()), sa.column("is_statutory", sa.Boolean()),
        sa.column("applies_to_all", sa.Boolean()), sa.column("validity_months", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(course_t, [
        {"id": _id("course", code), "code": code, "name": name, "is_statutory": True,
         "applies_to_all": all_staff, "validity_months": months, "is_active": True}
        for code, name, all_staff, months in _COURSES
    ])

    # The single settings row. Defaults match the escalation schedule the HSE
    # Manager set: reminder three days before due, supervisor at five days
    # overdue, HSE Manager at ten.
    op.execute("INSERT INTO ehs_config (id) VALUES (1) ON CONFLICT (id) DO NOTHING")


def downgrade() -> None:
    op.execute("DELETE FROM ehs_config WHERE id = 1")
    op.execute("DELETE FROM ehs_courses")
    op.execute("DELETE FROM ehs_holidays")
    op.execute("DELETE FROM ehs_vocabulary_items")
    op.execute("DELETE FROM ehs_vocabularies")
