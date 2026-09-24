"""The twelve incident types HSE's form offers, and what each one means to us.

HSE's Medical form widened from three incident types to twelve on 31 August.
Those twelve are one dropdown to the person filling the form, but they are not
one dimension: some say how badly someone was hurt, two say the Ministry must be
told, and the rest say what kind of event it was. Recording them in a single
column would lose information that the statutory clocks and the injury rates
both depend on — a fatality is always also a lost-time injury, so a single field
that holds "Fatality" has silently dropped a lost-time count.

So the form keeps their twelve options and this module fans them out into the
fields that already exist. The mapping lives here, on the server, because the
list will change again and a copy in the frontend would drift from a copy in an
import script.

Two of the twelve deliberately do NOT set an injury class:

    Fatality and Unconscious are `mol_reportable`, and say nothing about lost
    time. The form asks the follow-up question rather than letting this module
    guess, because guessing "lost_time" would be right nearly always and wrong
    exactly when a death is instantaneous — and a wrong LTIFR that nobody
    entered is worse than one question.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class IncidentType:
    code: str
    label: str
    # Which of the three forms this type is reported on.
    form_kind: str
    injury_class: str | None = None
    mol_reportable: bool = False
    # Vocabulary item code in `incident_category`, for the five that are neither
    # an injury severity nor a form kind.
    category_code: str | None = None
    # True where the type says nothing about injury severity and the reporter
    # must be asked separately.
    asks_injury_class: bool = False
    # True where the type is one where somebody was actually hurt, and the
    # WSIB detail — Section B on HSE's form — is worth asking for while the
    # first aider still remembers it. Occupational illness is in because it is
    # an injury for WSIB's purposes even though it carries no severity here.
    injury_detail: bool = False


TYPES: tuple[IncidentType, ...] = (
    IncidentType("report_only", "Report Only / No Injury", "medical",
                 category_code="report_only_no_injury"),
    IncidentType("near_miss", "Near Miss — no injury but has the potential to cause one",
                 "near_miss"),
    IncidentType("first_aid", "First Aid Injury — managed using existing first aid equipment",
                 "medical", injury_class="first_aid", injury_detail=True),
    IncidentType("medical_aid",
                 "Medical Aid by clinic, hospital or other health care facility",
                 "medical", injury_class="medical_aid", injury_detail=True),
    IncidentType("lost_time", "Lost Time as defined by WSIB", "medical",
                 injury_class="lost_time", injury_detail=True),
    IncidentType("property_damage", "Property / Asset Damage", "equipment"),
    IncidentType("occupational_illness", "Occupational Illness", "medical",
                 category_code="occupational_illness", injury_detail=True),
    IncidentType("environmental", "Environmental Incident", "equipment",
                 category_code="environmental_incident"),
    IncidentType("security", "Security Incident", "medical",
                 category_code="security_incident"),
    IncidentType("drug_alcohol", "Drug / Alcohol Related", "medical",
                 category_code="drug_alcohol_related"),
    IncidentType("fatality", "Fatality — MOL and police must be notified immediately",
                 "medical", mol_reportable=True, asks_injury_class=True,
                 injury_detail=True),
    IncidentType("unconscious", "Unconscious — MOL must be notified immediately",
                 "medical", mol_reportable=True, asks_injury_class=True,
                 injury_detail=True),
)

BY_CODE = {t.code: t for t in TYPES}


def for_form(form_kind: str) -> list[IncidentType]:
    """The types offered on one of the three forms."""
    return [t for t in TYPES if t.form_kind == form_kind]
