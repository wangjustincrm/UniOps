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
    # True where the picker is shown. Round 5 widened this: HSE asked for the
    # question to appear on the three types that imply their own answer too, so
    # that what the dashboard counts is visible on the form rather than
    # inferred behind it.
    asks_injury_class: bool = False
    # What the picker opens on when this type is chosen. Round 5: "when
    # selecting unconscious, automatically default to medical aid … first aid
    # injury should default the HB picker to first aid. lost time kind of
    # incident is a HB lost time", and from the same round's answers, "if we
    # record a Report Only, no injury can be inferred".
    #
    # ★ A default, not a rule. The reporter can change it and their choice is
    # what is stored — `crud.create` reads `payload.injury_class or
    # t.injury_class`, so the picker wins and the type is only the fallback.
    # A first-aid case that turns out to be lost time is a real thing and the
    # form must not argue with the person filling it in.
    default_injury_class: str | None = None
    # True where the type is one where somebody was actually hurt, and the
    # WSIB detail — Section B on HSE's form — is worth asking for while the
    # first aider still remembers it. Occupational illness is in because it is
    # an injury for WSIB's purposes even though it carries no severity here.
    injury_detail: bool = False


TYPES: tuple[IncidentType, ...] = (
    IncidentType("report_only", "Report Only / No Injury", "medical",
                 category_code="report_only_no_injury",
                 asks_injury_class=True, default_injury_class="none"),
    # ★ Asked on everything except the two HSE named. Round 4: "add 'how bad was
    # the person hurt' to all 'what kind of incident was this' to allow for
    # dashboard reporting, except report only/no injury and fatality selections."
    #
    # Left blank means nobody was hurt, which is how a near miss or a damaged
    # machine normally reads — the question is offered because either can turn
    # out to have hurt somebody, and until now those types carried no severity at
    # all and were invisible to every injury figure.
    IncidentType("near_miss", "Near Miss — no injury but has the potential to cause one",
                 "near_miss", asks_injury_class=True),
    # ★ These three *are* the severity, so the picker opens on the matching
    # answer rather than being hidden. Round 5 asked for it to be shown: the
    # dashboard counts this field, and a figure driven by something the form
    # never displayed is one nobody can check.
    IncidentType("first_aid", "First Aid Injury — managed using existing first aid equipment",
                 "medical", injury_class="first_aid", injury_detail=True,
                 asks_injury_class=True, default_injury_class="first_aid"),
    IncidentType("medical_aid",
                 "Medical Aid by clinic, hospital or other health care facility",
                 "medical", injury_class="medical_aid", injury_detail=True,
                 asks_injury_class=True, default_injury_class="medical_aid"),
    IncidentType("lost_time", "Lost Time as defined by WSIB", "medical",
                 injury_class="lost_time", injury_detail=True,
                 asks_injury_class=True, default_injury_class="lost_time"),
    IncidentType("property_damage", "Property / Asset Damage", "equipment",
                 asks_injury_class=True),
    IncidentType("occupational_illness", "Occupational Illness", "medical",
                 category_code="occupational_illness", asks_injury_class=True,
                 injury_detail=True),
    IncidentType("environmental", "Environmental Incident", "equipment",
                 category_code="environmental_incident", asks_injury_class=True),
    IncidentType("security", "Security Incident", "medical",
                 category_code="security_incident", asks_injury_class=True),
    IncidentType("drug_alcohol", "Drug / Alcohol Related", "medical",
                 category_code="drug_alcohol_related", asks_injury_class=True),
    # ★ Not asked, and classed as lost time regardless. HSE excluded fatality
    # from the question; a death is also, always, time lost, and leaving the
    # class empty would drop it out of LTIFR — the one figure where a fatality
    # most needs to appear. So the answer is not asked for because it is not in
    # doubt, rather than because it does not matter.
    IncidentType("fatality", "Fatality — MOL and police must be notified immediately",
                 "medical", mol_reportable=True, injury_class="lost_time",
                 injury_detail=True),
    IncidentType("unconscious", "Unconscious — MOL must be notified immediately",
                 "medical", mol_reportable=True, asks_injury_class=True,
                 default_injury_class="medical_aid", injury_detail=True),
)

BY_CODE = {t.code: t for t in TYPES}


def for_form(form_kind: str) -> list[IncidentType]:
    """The types offered on one of the three forms."""
    return [t for t in TYPES if t.form_kind == form_kind]
