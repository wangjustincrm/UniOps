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
    # Round 12, INC-01: a type only some people may pick. The permission
    # whose holders may; None means everyone who can report. HSE named
    # "coord, supervisor, or HSE" — exactly the holders of the investigate key.
    pick_permission: str | None = None
    # Round 12, GEN-01: a type HSE added in Settings rather than one of these.
    # It sets its label, form, category and who may pick it — never the legal
    # flags: it carries no injury class and is never Ministry-reportable.
    custom: bool = False
    category_id: object | None = None
    category_label: str | None = None


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
    #
    # ★ Round 12, Q4 reverses that for near misses: "a near miss by definition
    # is a process to identify things that could have injured someone but did
    # not. If a worker is injured it is no longer a near miss, its an injury
    # and will be documented as such." Not asked, and stored as no injury.
    IncidentType("near_miss", "Near Miss — no injury but has the potential to cause one",
                 "near_miss"),
    # Round 12, INC-01 and REP-01: a food-safety near miss, in the Food Safety
    # category, offered only to coordinators, supervisors and HSE. Counted with
    # the near misses wherever they are counted — they count the form.
    IncidentType("near_miss_food_safety", "Near Miss — Food Safety", "near_miss",
                 category_code="FOOD", pick_permission="ehs.incident.investigate"),
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

BUILTIN = {t.code: t for t in TYPES}
CUSTOM_PREFIX = "custom_"

# ★ HSE's own types (Settings → Incident types), read from the database at the
# start of every request by `load` — see app/api/v1/__init__.py — so every
# worker sees an addition at once. Lookups stay synchronous because pydantic
# validators and computed fields use them.
_custom: dict[str, IncidentType] = {}
_custom_order: list[str] = []
_active: set[str] = set()


class _ByCode(dict):
    """The built-in types and HSE's, as one mapping."""

    def get(self, key, default=None):  # type: ignore[override]
        return BUILTIN.get(key) or _custom.get(key, default)

    def __getitem__(self, key):
        t = self.get(key)
        if t is None:
            raise KeyError(key)
        return t

    def __contains__(self, key):
        return key in BUILTIN or key in _custom

    def __iter__(self):
        return iter([*BUILTIN, *_custom_order])

    def __len__(self):
        return len(BUILTIN) + len(_custom)


BY_CODE = _ByCode()


def is_offered(code: str) -> bool:
    """A built-in type, or one of HSE's not retired. A retired one still
    reads on the records that name it, but is not chosen anew (review L7)."""
    return code in BUILTIN or code in _active


def all_types(*, include_retired: bool = False) -> list[IncidentType]:
    return [*TYPES, *(_custom[c] for c in _custom_order if include_retired or c in _active)]


async def load(db) -> None:
    """Refresh HSE's types from the database. Retired ones stay resolvable —
    a record that names one still reads — but are not offered."""
    from sqlalchemy import select
    from app.models.incident_type import CustomIncidentType
    rows = (await db.execute(select(CustomIncidentType).order_by(
        CustomIncidentType.sort_order, CustomIncidentType.label))).scalars().all()
    fresh, order, active = {}, [], set()
    for r in rows:
        fresh[r.code] = IncidentType(
            r.code, r.label, r.form_kind,
            # The form asks how badly someone was hurt, as the built-in types
            # on that form do; a near miss does not (Q4).
            asks_injury_class=r.form_kind != "near_miss",
            injury_detail=r.form_kind == "medical",
            pick_permission=r.pick_permission, custom=True,
            category_id=r.category_id, category_label=r.category_label)
        order.append(r.code)
        if r.is_active:
            active.add(r.code)
    _custom.clear()
    _custom.update(fresh)
    _custom_order[:] = order
    _active.clear()
    _active.update(active)


def for_form(form_kind: str) -> list[IncidentType]:
    """The types offered on one of the three forms."""
    return [t for t in all_types() if t.form_kind == form_kind]
