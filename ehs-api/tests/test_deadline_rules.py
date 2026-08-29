"""Which statutory clocks an incident starts.

These are the rules an auditor asks about, and the ones where being wrong is a
regulatory failure rather than a bug. They are tested against the plain model
object — no database — because the decision is pure.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models.incident import Incident
from app.services.deadlines import clocks_for_incident

ET = ZoneInfo("America/Toronto")

OCCURRED = datetime(2026, 8, 28, 22, 10, tzinfo=ET)
AWARE = datetime(2026, 8, 29, 8, 30, tzinfo=ET)  # next morning


def _incident(**kw) -> Incident:
    base = dict(
        incident_no="INC-2026-0001", form_kind="medical", title="t",
        occurred_at=OCCURRED, employer_aware_at=AWARE,
        injury_class=None, mol_reportable=False,
    )
    base.update(kw)
    return Incident(**base)


# ── WSIB: which injuries owe a Form 7 ───────────────────────────────────────

def test_first_aid_alone_owes_nothing():
    """First aid treatment is recorded under Reg 1101 but is not a WSIB
    reportable injury on its own."""
    assert clocks_for_incident(_incident(injury_class="first_aid")) == []


def test_medical_aid_starts_the_wsib_clock():
    clocks = clocks_for_incident(_incident(injury_class="medical_aid"))
    assert [k for k, _ in clocks] == ["wsib_form7"]


def test_lost_time_starts_the_wsib_clock():
    clocks = clocks_for_incident(_incident(injury_class="lost_time"))
    assert [k for k, _ in clocks] == ["wsib_form7"]


def test_an_unclassified_incident_starts_nothing():
    """A near miss, or an injury not yet triaged, owes nothing yet."""
    assert clocks_for_incident(_incident(form_kind="near_miss")) == []


# ── MOL: the separate, orthogonal flag ──────────────────────────────────────

def test_mol_reportable_starts_the_mol_clock_on_its_own():
    """A critical injury is reportable even before anyone has decided whether
    it will cost time — the two facts are independent."""
    clocks = clocks_for_incident(_incident(mol_reportable=True))
    assert [k for k, _ in clocks] == ["mol_48h"]


def test_lost_time_and_mol_reportable_start_both_clocks():
    """The case a single five-level severity list cannot express, and the
    reason the model keeps two columns."""
    clocks = dict(clocks_for_incident(_incident(injury_class="lost_time", mol_reportable=True)))
    assert set(clocks) == {"mol_48h", "wsib_form7"}


# ── The two clocks measure from different instants ──────────────────────────

def test_mol_measures_from_the_occurrence_not_from_awareness():
    """OHSA s.51(1): 'within forty-eight hours after the occurrence'."""
    clocks = dict(clocks_for_incident(_incident(mol_reportable=True)))
    assert clocks["mol_48h"] == OCCURRED
    assert clocks["mol_48h"] != AWARE


def test_wsib_measures_from_employer_awareness():
    clocks = dict(clocks_for_incident(_incident(injury_class="lost_time")))
    assert clocks["wsib_form7"] == AWARE


def test_the_two_clocks_start_from_different_instants_on_one_incident():
    clocks = dict(clocks_for_incident(_incident(injury_class="lost_time", mol_reportable=True)))
    assert clocks["mol_48h"] == OCCURRED
    assert clocks["wsib_form7"] == AWARE
    assert clocks["mol_48h"] != clocks["wsib_form7"]


def test_wsib_falls_back_to_the_occurrence_when_awareness_is_unrecorded():
    """Better a deadline that is too early than a legal clock that never
    starts because a field was left blank."""
    clocks = dict(clocks_for_incident(
        _incident(injury_class="medical_aid", employer_aware_at=None)))
    assert clocks["wsib_form7"] == OCCURRED


def test_nothing_starts_without_an_occurrence_time():
    """Both clocks need an origin; a draft with no time yet owes nothing."""
    assert clocks_for_incident(_incident(
        injury_class="lost_time", mol_reportable=True,
        occurred_at=None, employer_aware_at=None)) == []
