"""HSE's real reference lists — the system stops going live with empty dropdowns.

Four spreadsheets and a completed incident form arrived on 31 August. Every one
of the lists turned out to be two levels deep — a main group with sub-items —
which is the shape `ehs_vocabulary_items` was built for, so they load without a
model change.

Three judgement calls are recorded here because they are not obvious from the
files alone:

**Immediate cause vs root cause.** HSE said the two "use the same list", and the
spreadsheet holds both: seven entries standing on their own (Unsafe Design, Poor
Housekeeping…) and twenty-seven grouped under six headings (Equipment Failure,
Procedural, Communication, Engineering, Training, Personal Factors). The seven
are what their Equipment form actually records as an immediate cause — the
sample reads "Unsafe Design" — and the twenty-seven are a causal taxonomy, which
is the shape of a root cause. So they are split accordingly rather than
duplicated. If HSE meant all thirty-four in both places, that is a Settings
change, not a migration.

**incident_category.** The 31 August version of the Medical form widened its
incident type from three options to twelve. Five of those twelve — report only,
occupational illness, environmental, security, drug/alcohol — are neither an
injury severity nor a form kind, and had nowhere to go: `category_id` existed on
every EHS table and was never populated. They land here.

**body_part is incomplete and deliberately so.** The list is read from the
completed form, where the PDF's two-column layout interleaves it with another
field, so extraction stops partway. Nineteen entries are confirmed present;
hand, finger, foot and toe are conspicuously absent and almost certainly exist.
HSE asked for a manual-entry path for exactly this case, so the rest get added
in Settings rather than guessed at here. WSIB wording was requested and these
terms match it, but the full WSIB list has not been sighted.

`hazard_control` is a new vocabulary — it was not in the plan. It is NOT the
hierarchy of controls (elimination / substitution / engineering / administrative
/ PPE), which is an international standard and stays fixed; this is HSE's list of
specific control measures, and both are needed.

Revision ID: 20260831_0004
Revises: 20260831_0003
Create Date: 2026-08-31
"""
import re
import uuid

import sqlalchemy as sa
from alembic import op

revision = "20260831_0004"
down_revision = "20260831_0003"
branch_labels = None
depends_on = None

# Deterministic ids: a re-run seeds the same rows, and a downgrade/upgrade cycle
# does not orphan anything that referenced them.
_NS = uuid.UUID("6f1d9a2e-4c3b-4e7a-9f52-1b8c0d5e7a11")

# (vocabulary_code, is_hierarchical, [(group_or_item, [children])])
_SEED = [
    ("immediate_cause", False, [
        ("Guarding or lack of", []),
        ("Defective Tools or Equipment", []),
        ("Hazardous Arrangements", []),
        ("Unsafe Conditions", []),
        ("Unsafe Design", []),
        ("Poor Housekeeping", []),
        ("Environmental Conditions", []),
    ]),
    ("root_cause", True, [
        ("Equipment Failure", ["Unknown", "Defective", "Design", "Repeat Failure", "Preventive Maintenance"]),
        ("Procedural", ["No Procedure / SOP", "Error in Procedure", "Too Complex", "Not Followed"]),
        ("Communication", ["Between Shifts / Depts", "Failure to Agree", "Failure to Understand", "Language / Literacy"]),
        ("Engineering", ["Layout / Design", "Poor Lighting / Guarding", "Poorly Identified", "Noise / Light / Vibration", "Ergonomics"]),
        ("Training", ["Individual not trained", "No Training Available", "Unintentional Oversight", "Intentional Oversight", "Inexperienced"]),
        ("Personal Factors", ["Distracted", "Not Motivated", "Rushing", "Fatigued"]),
    ]),
    ("hazard", True, [
        ("Physical", ["working at heights", "line of fire/body position", "slips/trips/falls", "slopes and terrain", "compressed gas/air", "entry/exit into the space", "ignition sources", "hot work", "fires/burns", "pinch points", "others working/simultaneous operations", "grinding", "hand/power tools", "falling objects", "suspended loads", "hoisting/overhead work", "congested area", "mobile equipment/vehicles", "sharp edges/material", "excavation/cave-in", "engulfment", "drowning", "entrapment", "security", "Hot steam", "Hot water", "Hot surfaces", "Lifting heavy loads", "Required to work off ladder", "Required to work near pipes, other hazards that could injure the head"]),
        ("Energy", ["moving equipment", "electrical less than 480V", "electrical greater than or equal to 480V", "pneumatic", "hydraulic", "mechanical", "pressure", "Air", "Steam", "Fluids", "radiation", "fluids and gases", "energized equipment", "static/induced voltage"]),
        ("Atmospheric and Chemical", ["asphyxiation", "O2 deficiency", "O2 enrichment", "explosive/flammable materials", "corrosive material (acids/caustics)", "hydrogen sulfide", "carbon monoxide", "welding fumes", "cleaners/degreasers", "gasoline or diesel", "pipeline coating", "herbicides/pesticides", "natural gas", "mercaptan", "highly reactive substances", "Oxonia Acid sanitizer", "Foam Force acid cleaner"]),
        ("Environmental", ["cold environment", "hot environment", "dusty environment", "poor lighting (bright/dark)", "waste/debris", "noise", "spills", "awkward position", "unstable position", "heavy/awkward loads", "traffic in area", "weather conditions", "dry surroundings", "damp/wet conditions", "poor housekeeping", "vibration", "fatigue", "overexertion", "manual lifting", "repetitive motion"]),
        ("Biological", ["mould", "biological waste", "poisonous plant material", "exposure to animals/insects"]),
    ]),
    ("ppe", True, [
        ("Chemical Exposure Items", ["Chemical Gloves", "Chemical Face Shield", "Chemical Smock", "Chemical Googles"]),
        ("Body Protection", ["Steel Toed footware", "Fire/Heat Resistant Clothing", "High Visibility Clothing / Vest Required"]),
        ("Head Protection", ["Hard Hat", "Welding Helmet", "Bump Cap", "Confined space helmet"]),
        ("Eye/Face Protection", ["Safety Glasses", "Face Shield", "Goggles", "Welding Shield"]),
        ("Fall Protection", ["Fall Arrest Harness", "Hearing Protection", "Single-use Earplugs", "Over the ear muffs"]),
        ("Respiration Protection", ["N95 Mask", "Medical type Mask", "Half face mask with filter canister", "Full Face mask with filter canister"]),
        ("Hand Protection", ["Cut Resistant Gloves", "Leather Gloves", "Maintenance Gloves", "Mechanics Gloves", "Chemical Resistant Gloves", "Nitrile Gloves"]),
    ]),
    ("hazard_control", True, [
        ("Engineering/Equipment", ["energy/equipment isolation (LOTO) (see separate Docuement)", "Natural Ventilation", "Mechanical Ventilation", "additional lighting (flashlights / Other)", "mechanical lifting device", "adequate explosion proof lighting", "explosion proof extension cords", "explosion proof electrical", "fire extinguisher", "tag line for loads", "barriers/barricades/guards", "2-way radios", "traffic control devices/signs", "air shut offs", "first aid kit", "welding screen", "spill kit/absorbent material", "Fall Arrest Harness & Lanyard", "Review Anchorage point for fall arrest lanyard", "Spotter", "Xtirpa Confined space equipment", "Tripod rescue system (WWTP)", "ladders/stairs", "Requires working off a ladder", "Will require working off a scaffolding platform", "Scaffolding required for safe work"]),
        ("Administrative", ["specific work/entry procedure", "fall protection plan", "housekeeping", "inspection (area/tools)", "correct body placement", "spotter", "work/rest cycles", "signage", "ergonomic assessment", "Wearing lifting belt", "Proper lifting technique (using knees not back)"]),
        ("Electrical", ["electrical safe limits of approach", "high voltage probe/gloves", "GFI protection", "arc flash PPE (as per NFPA 70E)", "voltmeter not used on conductors greater than 600v", "competent & qualified electrical workers", "test & confirm voltage on electrical conductors"]),
        ("Biological", ["Proper disposal of waste", "Control insects/wildlife", "Environmental impact controlled"]),
    ]),
    ("incident_category", False, [
        ("Report Only / No Injury", []),
        ("Occupational Illness", []),
        ("Environmental Incident", []),
        ("Security Incident", []),
        ("Drug / Alcohol Related", []),
    ]),
    ("body_part", False, [
        ("Head", []),
        ("Face", []),
        ("Teeth", []),
        ("Eye", []),
        ("Mouth", []),
        ("Ear", []),
        ("Neck", []),
        ("Shoulder", []),
        ("Arm", []),
        ("Elbow", []),
        ("Wrist", []),
        ("Chest", []),
        ("Back", []),
        ("Abdomen", []),
        ("Hip", []),
        ("Knee", []),
        ("Leg", []),
        ("Ankle", []),
        ("Other", []),
    ]),
]


def _slug(s: str, n: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:n].rstrip("_")


def upgrade() -> None:
    conn = op.get_bind()

    # A control-measure list HSE uses but the plan never had.
    conn.execute(sa.text("""
        insert into ehs_vocabularies (code, name, description, is_hierarchical, is_system_locked)
        values ('hazard_control', 'Hazard Control',
                'Specific control measures. Distinct from the hierarchy of controls, '
                'which is a fixed international standard.', true, false)
        on conflict (code) do nothing
    """))

    for vocab, hierarchical, groups in _SEED:
        conn.execute(sa.text(
            "update ehs_vocabularies set is_hierarchical = :h where code = :c"
        ), {"h": hierarchical, "c": vocab})

        for gi, (group, children) in enumerate(groups, start=1):
            gcode = _slug(group)
            gid = uuid.uuid5(_NS, f"{vocab}:{gcode}")
            conn.execute(sa.text("""
                insert into ehs_vocabulary_items
                    (id, vocabulary_code, parent_id, code, label, sort_order, is_active, attrs, path)
                values (:id, :v, null, :code, :label, :sort, true, '{}'::jsonb, :path)
                on conflict (vocabulary_code, code) do nothing
            """), {"id": gid, "v": vocab, "code": gcode, "label": group,
                   "sort": gi * 10, "path": group})

            for ci, child in enumerate(children, start=1):
                ccode = _slug(f"{gcode}_{_slug(child)}")
                conn.execute(sa.text("""
                    insert into ehs_vocabulary_items
                        (id, vocabulary_code, parent_id, code, label, sort_order, is_active, attrs, path)
                    values (:id, :v, :pid, :code, :label, :sort, true, '{}'::jsonb, :path)
                    on conflict (vocabulary_code, code) do nothing
                """), {"id": uuid.uuid5(_NS, f"{vocab}:{ccode}"), "v": vocab, "pid": gid,
                       "code": ccode, "label": child, "sort": ci * 10,
                       "path": f"{group}/{child}"})


def downgrade() -> None:
    conn = op.get_bind()
    for vocab, _h, _g in _SEED:
        conn.execute(sa.text(
            "delete from ehs_vocabulary_items where vocabulary_code = :c"), {"c": vocab})
    conn.execute(sa.text("delete from ehs_vocabularies where code = 'hazard_control'"))
