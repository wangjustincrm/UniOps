"""transform() pure-function tests.

Field names are the real NC65 columns confirmed by the 2026-08-03 survey
(docs/superpowers/specs/2026-08-03-nc-bom-survey.md) — NOT the
task-5-brief's guessed pk_bom/pk_invmandoc/hstate/basenum/wastagerate names,
which predate the survey. Real header PK = CBOMID, parent material =
HCMATERIALID, version = HVERSION, approval status = FBILLSTATUS; line PK =
CBOM_BID, component = CMATERIALID, quantity = NITEMNUM, row no = VROWNO,
effective window = CBEGINPERIOD/CENDPERIOD; substitute PK = CBOM_REPLACEID,
substitute material = CREPLMATERIALOID. The brief's `wastagerate` field does
not exist in real NC65 BD_BOM_B — scrap_rate has no NC source at all in this
instance (survey §7) and always defaults to 0, so no test exercises a
"percentage -> decimal" scrap_rate mapping; see test_scrap_rate_always_zero.
"""
from decimal import Decimal

from app.services.nc_bom_sync.transform import transform


def _raw(headers=None, lines=None, repl=None, material_codes=None):
    return {
        "headers": headers or [],
        "lines": lines or [],
        "repl": repl or [],
        "material_codes": material_codes or {},
    }


def test_transform_maps_header_and_lines():
    raw = _raw(
        headers=[{
            "cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0",
            "fbillstatus": 1, "pk_org": "ORG1", "hvchangerate": "1/1",
        }],
        lines=[{
            "cbom_bid": "PKB1", "cbomid": "PK1", "cmaterialid": "M2",
            "nitemnum": Decimal("1.05"), "vrowno": "10",
            "cbeginperiod": "2019-09-01 00:00:00", "cendperiod": "2999-12-31 23:59:59",
        }],
        material_codes={"M1": "CF0086", "M2": "CW0040"},
    )
    out = transform(raw)

    b = out["boms"][0]
    assert b["product_material_code"] == "CF0086" and b["version"] == "1.0"
    assert b["nc_source_pk"] == "PK1"
    assert b["status"] == "approved"

    ln = out["lines"][0]
    assert ln["component_material_code"] == "CW0040"
    assert ln["qty_per"] == Decimal("1.05")
    assert ln["scrap_rate"] == Decimal("0")
    assert ln["line_no"] == 10
    assert ln["nc_source_pk"] == "PKB1"


def test_transform_skips_unresolvable_material():
    raw = _raw(
        headers=[{"cbomid": "PK1", "hcmaterialid": "MISSING", "hversion": "1", "fbillstatus": 1}],
        material_codes={},
    )
    out = transform(raw)
    assert out["boms"] == []
    assert out["skipped"] == ["PK1"]


def test_transform_drops_orphan_lines_when_header_skipped():
    """A line whose parent header failed to resolve must not become a
    dangling bom_line with no bom to attach to."""
    raw = _raw(
        headers=[{"cbomid": "PK1", "hcmaterialid": "MISSING", "hversion": "1", "fbillstatus": 1}],
        lines=[{"cbom_bid": "PKB1", "cbomid": "PK1", "cmaterialid": "M2", "nitemnum": 5, "vrowno": "10"}],
        material_codes={"M2": "CM0040"},
    )
    out = transform(raw)
    assert out["boms"] == []
    assert out["lines"] == []


def test_bom_type_derived_from_material_code_prefix():
    raw = _raw(
        headers=[
            {"cbomid": "PK-CS", "hcmaterialid": "MCS", "hversion": "1.0", "fbillstatus": 1},
            {"cbomid": "PK-CW", "hcmaterialid": "MCW", "hversion": "1.0", "fbillstatus": 1},
            {"cbomid": "PK-CF", "hcmaterialid": "MCF", "hversion": "1.0", "fbillstatus": 1},
            {"cbomid": "PK-XX", "hcmaterialid": "MXX", "hversion": "1.0", "fbillstatus": 1},
        ],
        material_codes={"MCS": "CS0026", "MCW": "CW0001", "MCF": "CF0092", "MXX": "ZZ9999"},
    )
    out = transform(raw)
    by_pk = {b["nc_source_pk"]: b["bom_type"] for b in out["boms"]}
    assert by_pk["PK-CS"] == "milling"
    assert by_pk["PK-CW"] == "drymix"
    assert by_pk["PK-CF"] == "packaging"
    assert by_pk["PK-XX"] == "unknown"
    # Unknown prefixes must be surfaced, never silently dropped.
    assert any(w["nc_source_pk"] == "PK-XX" for w in out["warnings"])


def test_status_mapping_from_fbillstatus():
    raw = _raw(
        headers=[
            {"cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1},
            {"cbomid": "PK2", "hcmaterialid": "M1", "hversion": "2.0", "fbillstatus": -1},
            {"cbomid": "PK3", "hcmaterialid": "M1", "hversion": "3.0", "fbillstatus": 99},
        ],
        material_codes={"M1": "CF0001"},
    )
    out = transform(raw)
    by_pk = {b["nc_source_pk"]: b["status"] for b in out["boms"]}
    assert by_pk["PK1"] == "approved"
    assert by_pk["PK2"] == "draft"
    assert by_pk["PK3"] == "inactive"


def test_scrap_rate_always_zero_no_nc_source_data():
    """NC's candidate loss columns are NULL across all sampled BD_BOM_B rows
    in this instance (survey §7) — scrap_rate must default to 0 regardless
    of what junk is thrown at the loss-candidate fields, never invent a
    mapping from them."""
    raw = _raw(
        headers=[{"cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1}],
        lines=[{
            "cbom_bid": "PKB1", "cbomid": "PK1", "cmaterialid": "M2",
            "nitemnum": 100, "vrowno": "10",
            "nbfixshrinknum": None, "ndissipationum": None,
        }],
        material_codes={"M1": "CF0001", "M2": "CR0001"},
    )
    out = transform(raw)
    assert out["lines"][0]["scrap_rate"] == Decimal("0")


def test_yield_rate_parsed_from_hvchangerate_ratio():
    raw = _raw(
        headers=[
            {"cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1, "hvchangerate": "1000/1"},
            {"cbomid": "PK2", "hcmaterialid": "M1", "hversion": "1.1", "fbillstatus": 1, "hvchangerate": "1/1"},
            {"cbomid": "PK3", "hcmaterialid": "M1", "hversion": "1.2", "fbillstatus": 1, "hvchangerate": None},
        ],
        material_codes={"M1": "CF0001"},
    )
    out = transform(raw)
    by_pk = {b["nc_source_pk"]: b["yield_rate"] for b in out["boms"]}
    assert by_pk["PK1"] == Decimal("1000")
    assert by_pk["PK2"] == Decimal("1")
    assert by_pk["PK3"] == Decimal("1")  # blank -> safe default, not a crash


def test_line_effective_window_parsed_and_nc_tilde_placeholder_is_blank():
    """CBEGINPERIOD/CENDPERIOD land on the LINE, not the header (survey §8).
    NC's '~' empty-value placeholder must be treated as blank, not a literal
    string value."""
    raw = _raw(
        headers=[{"cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1}],
        lines=[{
            "cbom_bid": "PKB1", "cbomid": "PK1", "cmaterialid": "M2",
            "nitemnum": 1, "vrowno": "10",
            "cbeginperiod": "2019-09-01 00:00:00", "cendperiod": "2999-12-31 23:59:59",
        }, {
            "cbom_bid": "PKB2", "cbomid": "PK1", "cmaterialid": "M2",
            "nitemnum": 1, "vrowno": "20",
            "cbeginperiod": "~", "cendperiod": "~",
        }],
        material_codes={"M1": "CF0001", "M2": "CR0001"},
    )
    out = transform(raw)
    from datetime import date
    ln1, ln2 = out["lines"]
    assert ln1["effective_from"] == date(2019, 9, 1)
    assert ln1["effective_to"] == date(2999, 12, 31)
    assert ln2["effective_from"] is None
    assert ln2["effective_to"] is None


def test_substitutes_mapped_and_orphans_dropped():
    raw = _raw(
        headers=[{"cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1}],
        lines=[{"cbom_bid": "PKB1", "cbomid": "PK1", "cmaterialid": "M2", "nitemnum": 1, "vrowno": "10"}],
        repl=[
            {"cbom_replaceid": "R1", "cbom_bid": "PKB1", "creplmaterialoid": "M3", "vrowno": "10"},
            # Orphan: parent line PKB-MISSING never made it into `lines`.
            {"cbom_replaceid": "R2", "cbom_bid": "PKB-MISSING", "creplmaterialoid": "M3", "vrowno": "10"},
        ],
        material_codes={"M1": "CF0001", "M2": "CR0001", "M3": "CR0002"},
    )
    out = transform(raw)
    assert len(out["substitutes"]) == 1
    sub = out["substitutes"][0]
    assert sub["substitute_material_code"] == "CR0002"
    assert sub["priority"] == 10
    assert sub["mode"] == "suggest"
    assert sub["nc_source_pk"] == "R1"


# ---------------------------------------------------------------------------
# PATCH 1: S-prefixed finished goods must map to bom_type='packaging', not
# 'unknown'. Verified live: S0093's BOM = CW dry-mix powder + 7 CP packaging
# items, structurally identical to CF0092's.
# ---------------------------------------------------------------------------

def test_s_prefix_finished_good_maps_to_packaging_bom_type():
    raw = _raw(
        headers=[
            {"cbomid": "PK-S", "hcmaterialid": "MS", "hversion": "1.0", "fbillstatus": 1},
        ],
        material_codes={"MS": "S0093"},
    )
    out = transform(raw)
    b = out["boms"][0]
    assert b["bom_type"] == "packaging"
    # Must not be double-counted as an "unknown prefix" warning.
    assert out["warnings"] == []


def test_s_prefix_does_not_collide_with_two_char_cs_cw_cf_prefixes():
    """'S' is checked as a 1-char prefix specifically so it can't shadow the
    2-char CS/CW/CF table — a code starting with 'C' must never fall into
    the S bucket."""
    raw = _raw(
        headers=[
            {"cbomid": "PK-CS", "hcmaterialid": "MCS", "hversion": "1.0", "fbillstatus": 1},
        ],
        material_codes={"MCS": "CS0026"},
    )
    out = transform(raw)
    assert out["boms"][0]["bom_type"] == "milling"


# ---------------------------------------------------------------------------
# PATCH 2: bom_lines.uom must resolve BD_MEASDOC pks to real unit codes, not
# store the raw NC pk. Unresolvable pks -> null + warning, never a crash.
# ---------------------------------------------------------------------------

def test_uom_resolved_from_measdoc_pk_lookup():
    raw = _raw(
        headers=[{"cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1, "hnparentnum": 1}],
        lines=[{
            "cbom_bid": "PKB1", "cbomid": "PK1", "cmaterialid": "M2",
            "nitemnum": 1, "vrowno": "10", "cmeasureid": "PK-MEAS-KGM",
        }],
        material_codes={"M1": "CF0001", "M2": "CR0001"},
    )
    raw["uoms"] = {"PK-MEAS-KGM": "KGM"}
    out = transform(raw)
    ln = out["lines"][0]
    assert ln["uom"] == "KGM"
    assert out["warnings"] == []


def test_unresolvable_uom_pk_leaves_null_and_warns_without_crashing():
    raw = _raw(
        headers=[{"cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1}],
        lines=[{
            "cbom_bid": "PKB1", "cbomid": "PK1", "cmaterialid": "M2",
            "nitemnum": 1, "vrowno": "10", "cmeasureid": "PK-MEAS-GHOST",
        }],
        material_codes={"M1": "CF0001", "M2": "CR0001"},
    )
    raw["uoms"] = {}  # ghost pk never resolves
    out = transform(raw)
    ln = out["lines"][0]
    assert ln["uom"] is None
    assert any(
        w["nc_source_pk"] == "PKB1" and w["reason"] == "unresolved_uom_pk" and w["field"] == "uom"
        for w in out["warnings"]
    )


def test_blank_uom_pk_resolves_to_none_without_warning():
    """A line that simply has no cmeasureid at all is the normal case, not a
    resolution failure — must not spuriously warn."""
    raw = _raw(
        headers=[{"cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1, "hnparentnum": 1}],
        lines=[{"cbom_bid": "PKB1", "cbomid": "PK1", "cmaterialid": "M2", "nitemnum": 1, "vrowno": "10"}],
        material_codes={"M1": "CF0001", "M2": "CR0001"},
    )
    out = transform(raw)
    assert out["lines"][0]["uom"] is None
    assert out["warnings"] == []


# ---------------------------------------------------------------------------
# PATCH 3: secondary (assistant) unit qty/uom from NASSITEMNUM/CASSMEASUREID
# must be captured, not dropped.
# ---------------------------------------------------------------------------

def test_secondary_qty_and_uom_populated_for_two_unit_lines():
    """Real S0093 numbers (live NC65, 2026-08-04 spot-check): header
    HNPARENTNUM=420, HNASSPARENTNUM=100. The CP0115-1 tin line's
    NITEMNUM=NASSITEMNUM=610 must normalize to qty_per=610/420 (primary
    unit, KGM) and qty_per_secondary=610/100 (secondary unit, EA) — TWO
    DIFFERENT ratios, not the same raw 610 stored twice (the PATCH 6 bug:
    NITEMNUM/NASSITEMNUM are whole-BATCH quantities, not per-unit ones)."""
    raw = _raw(
        headers=[{
            "cbomid": "PK-S", "hcmaterialid": "MS", "hversion": "1.0", "fbillstatus": 1,
            "hnparentnum": 420, "hnassparentnum": 100, "hvchangerate": "4.2/1",
        }],
        lines=[{
            "cbom_bid": "PKB1", "cbomid": "PK-S", "cmaterialid": "M2",
            "nitemnum": Decimal("610"), "vrowno": "10",
            "cmeasureid": "PK-KGM", "nassitemnum": Decimal("610"), "cassmeasureid": "PK-EA",
        }],
        material_codes={"MS": "S0093", "M2": "CP0115"},
    )
    raw["uoms"] = {"PK-KGM": "KGM", "PK-EA": "EA"}
    out = transform(raw)
    ln = out["lines"][0]
    assert ln["qty_per"] == Decimal("1.4523809524")  # 610/420, ~688g/tin — matches the real product spec
    assert ln["uom"] == "KGM"
    assert ln["qty_per_secondary"] == Decimal("6.1")  # 610/100, NOT the raw 610
    assert ln["uom_secondary"] == "EA"
    assert out["warnings"] == []  # both divisors present and valid -> no anomaly to report


def test_secondary_qty_and_uom_none_when_absent_not_zero():
    """A single-unit line (no NASSITEMNUM/CASSMEASUREID at all) must get
    None, not 0/blank — 0 would misread as 'zero pieces'."""
    raw = _raw(
        headers=[{"cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1, "hnparentnum": 1}],
        lines=[{"cbom_bid": "PKB1", "cbomid": "PK1", "cmaterialid": "M2", "nitemnum": 1, "vrowno": "10"}],
        material_codes={"M1": "CF0001", "M2": "CR0001"},
    )
    out = transform(raw)
    ln = out["lines"][0]
    assert ln["qty_per_secondary"] is None
    assert ln["uom_secondary"] is None
    # A line with no secondary unit at all must never touch/warn about
    # HNASSPARENTNUM — the lazy divisor resolution must not fire for it.
    assert out["warnings"] == []


# ---------------------------------------------------------------------------
# PATCH 6 (2026-08-04, CRITICAL defect fix): qty_per/qty_per_secondary must
# be normalized against the header's HNPARENTNUM/HNASSPARENTNUM batch-output
# quantity, not store NITEMNUM/NASSITEMNUM (a whole-BATCH quantity) as-is.
# ---------------------------------------------------------------------------

def test_qty_per_normalized_against_header_batch_output_qty():
    """Real CS0026 numbers (live NC65, 2026-08-04 spot-check): header
    HNPARENTNUM=1000. A CR0024 raw-material line with NITEMNUM=270 must
    normalize to qty_per=0.27 (a sensible recipe ratio), not the raw batch
    quantity 270."""
    raw = _raw(
        headers=[{
            "cbomid": "PK-CS", "hcmaterialid": "MCS", "hversion": "1.0", "fbillstatus": 1,
            "hnparentnum": 1000,
        }],
        lines=[{
            "cbom_bid": "PKB1", "cbomid": "PK-CS", "cmaterialid": "M2",
            "nitemnum": Decimal("270"), "vrowno": "10",
        }],
        material_codes={"MCS": "CS0026", "M2": "CR0024"},
    )
    out = transform(raw)
    ln = out["lines"][0]
    assert ln["qty_per"] == Decimal("0.27")
    assert out["warnings"] == []


def test_qty_per_quantized_to_ten_decimal_places_not_badly_rounded():
    """Real S0093 numbers: header HNPARENTNUM=420. CP0132's NITEMNUM=1 line
    normalizes to a repeating decimal (1/420 = 0.0023809523809...) — must
    keep enough precision that 6dp rounding wouldn't have provided."""
    raw = _raw(
        headers=[{
            "cbomid": "PK-S", "hcmaterialid": "MS", "hversion": "1.0", "fbillstatus": 1,
            "hnparentnum": 420,
        }],
        lines=[{
            "cbom_bid": "PKB1", "cbomid": "PK-S", "cmaterialid": "M2",
            "nitemnum": Decimal("1"), "vrowno": "10",
        }],
        material_codes={"MS": "S0093", "M2": "CP0132"},
    )
    out = transform(raw)
    assert out["lines"][0]["qty_per"] == Decimal("0.0023809524")


def test_missing_hnparentnum_falls_back_to_one_and_warns():
    """Zero of 1016 live NC65 headers have a NULL/0 HNPARENTNUM (survey
    spot-check), so this is a defensive path, not a routine one — but it
    must never crash or silently divide by zero: fall back to a no-op
    divisor of 1 and surface the anomaly in `warnings`."""
    raw = _raw(
        headers=[{"cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1}],
        lines=[{"cbom_bid": "PKB1", "cbomid": "PK1", "cmaterialid": "M2", "nitemnum": 5, "vrowno": "10"}],
        material_codes={"M1": "CF0001", "M2": "CR0001"},
    )
    out = transform(raw)
    assert out["lines"][0]["qty_per"] == Decimal("5")  # fallback divisor of 1 -> unchanged
    assert any(
        w["nc_source_pk"] == "PK1" and w["reason"] == "invalid_batch_divisor" and w["field"] == "hnparentnum"
        for w in out["warnings"]
    )


def test_zero_hnparentnum_falls_back_to_one_and_warns():
    raw = _raw(
        headers=[{"cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1, "hnparentnum": 0}],
        lines=[{"cbom_bid": "PKB1", "cbomid": "PK1", "cmaterialid": "M2", "nitemnum": 5, "vrowno": "10"}],
        material_codes={"M1": "CF0001", "M2": "CR0001"},
    )
    out = transform(raw)
    assert out["lines"][0]["qty_per"] == Decimal("5")
    assert any(w["reason"] == "invalid_batch_divisor" and w["field"] == "hnparentnum" for w in out["warnings"])


def test_header_with_no_lines_never_triggers_a_divisor_warning():
    """A header with zero surviving lines never needs a divisor at all —
    resolving (and warning about) one anyway would be pure noise. Divisor
    resolution must be lazy, keyed off an actual line needing it."""
    raw = _raw(
        headers=[{"cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1}],
        material_codes={"M1": "CF0001"},
    )
    out = transform(raw)
    assert out["warnings"] == []


def test_missing_hnassparentnum_falls_back_to_resolved_hnparentnum_not_straight_to_one():
    """HNASSPARENTNUM missing (but HNPARENTNUM present and valid) must fall
    back to HNPARENTNUM's own resolved divisor, not skip straight to 1."""
    raw = _raw(
        headers=[{
            "cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1,
            "hnparentnum": 420,
        }],
        lines=[{
            "cbom_bid": "PKB1", "cbomid": "PK1", "cmaterialid": "M2",
            "nitemnum": Decimal("420"), "nassitemnum": Decimal("420"), "vrowno": "10",
        }],
        material_codes={"M1": "CF0001", "M2": "CR0001"},
    )
    out = transform(raw)
    ln = out["lines"][0]
    assert ln["qty_per"] == Decimal("1")
    assert ln["qty_per_secondary"] == Decimal("1")  # 420 / fallback-to-HNPARENTNUM(420), not 420/1
    assert any(
        w["reason"] == "invalid_batch_divisor" and w["field"] == "hnassparentnum" for w in out["warnings"]
    )


# ---------------------------------------------------------------------------
# PATCH 6 follow-up (coordinator review, 2026-08-04): `bom_explode.py`'s
# decision to NOT divide by `yield_rate` rests on `yield_rate` always
# equaling `HNPARENTNUM/HNASSPARENTNUM` (verified by hand, never enforced).
# `_check_yield_rate_invariant` re-verifies this on every sync so a real NC
# divergence surfaces as a warning instead of silently mis-planning.
# ---------------------------------------------------------------------------

def test_yield_rate_matching_batch_ratio_does_not_warn():
    """S0093's real numbers: HNPARENTNUM=420, HNASSPARENTNUM=100,
    HVCHANGERATE='4.2/1' -> yield_rate=4.2 == 420/100 exactly. No anomaly."""
    raw = _raw(
        headers=[{
            "cbomid": "PK-S", "hcmaterialid": "MS", "hversion": "1.0", "fbillstatus": 1,
            "hnparentnum": 420, "hnassparentnum": 100, "hvchangerate": "4.2/1",
        }],
        material_codes={"MS": "S0093"},
    )
    out = transform(raw)
    assert out["warnings"] == []


def test_yield_rate_mismatching_batch_ratio_warns():
    """If NC ever populates HVCHANGERATE inconsistently with HNPARENTNUM/
    HNASSPARENTNUM, bom_explode.py's no-divide decision silently stops
    being correct for that BOM — this must be surfaced, not swallowed."""
    raw = _raw(
        headers=[{
            "cbomid": "PK-BAD", "hcmaterialid": "MS", "hversion": "1.0", "fbillstatus": 1,
            "hnparentnum": 420, "hnassparentnum": 100, "hvchangerate": "1/1",  # should be 4.2/1
        }],
        material_codes={"MS": "S0093"},
    )
    out = transform(raw)
    assert any(
        w["nc_source_pk"] == "PK-BAD" and w["reason"] == "yield_rate_batch_ratio_mismatch"
        and w["product_material_code"] == "S0093"
        for w in out["warnings"]
    )


def test_yield_rate_invariant_skipped_when_either_divisor_absent():
    """Only checked when BOTH HNPARENTNUM and HNASSPARENTNUM are present —
    a header simply missing one of them is already covered by
    _resolve_divisor's own (line-triggered) warning; this check must not
    double-warn for the same underlying gap."""
    raw = _raw(
        headers=[{
            "cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1,
            "hnparentnum": 420, "hvchangerate": "1/1",  # no hnassparentnum at all
        }],
        material_codes={"M1": "CF0001"},
    )
    out = transform(raw)
    assert not any(w["reason"] == "yield_rate_batch_ratio_mismatch" for w in out["warnings"])


# ---------------------------------------------------------------------------
# PATCH 4: NC's EA and PIECES both mean "个" for packaging materials — must
# normalize to the single canonical code EA, on both uom fields.
# ---------------------------------------------------------------------------

def test_ea_and_pieces_normalize_to_canonical_ea_on_both_uom_fields():
    raw = _raw(
        headers=[{
            "cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1,
            "hnparentnum": 1, "hnassparentnum": 1,
        }],
        lines=[{
            "cbom_bid": "PKB1", "cbomid": "PK1", "cmaterialid": "M2",
            "nitemnum": 1, "vrowno": "10",
            "cmeasureid": "PK-PIECES", "nassitemnum": 1, "cassmeasureid": "PK-EA",
        }],
        material_codes={"M1": "CF0001", "M2": "CP0001"},
    )
    raw["uoms"] = {"PK-PIECES": "PIECES", "PK-EA": "EA"}
    out = transform(raw)
    ln = out["lines"][0]
    assert ln["uom"] == "EA"          # was 'PIECES' pre-normalization
    assert ln["uom_secondary"] == "EA"  # already 'EA', unaffected
    assert out["warnings"] == []


# ---------------------------------------------------------------------------
# PATCH 5: CM (standardized milk, deprecated ~2 years ago) exclusion —
# (a) a header whose PARENT is CM-prefixed never syncs at all (skipped);
# (b) a line whose COMPONENT is CM-prefixed is dropped but visibly counted
#     in `warnings`, not silently discarded and not folded into `skipped`.
# No fallback/explosion through CM is expected or exercised here.
# ---------------------------------------------------------------------------

def test_cm_prefixed_parent_header_excluded_entirely():
    raw = _raw(
        headers=[
            {"cbomid": "PK-CM", "hcmaterialid": "MCM", "hversion": "1.0", "fbillstatus": 1},
            {"cbomid": "PK-CF", "hcmaterialid": "MCF", "hversion": "1.0", "fbillstatus": 1},
        ],
        lines=[
            # A line under the CM header must never surface as an orphan.
            {"cbom_bid": "PKB-CM", "cbomid": "PK-CM", "cmaterialid": "MCR", "nitemnum": 1, "vrowno": "10"},
        ],
        material_codes={"MCM": "CM0001", "MCF": "CF0001", "MCR": "CR0001"},
    )
    out = transform(raw)
    assert [b["nc_source_pk"] for b in out["boms"]] == ["PK-CF"]
    assert "PK-CM" in out["skipped"]
    assert out["lines"] == []
    assert "PKB-CM" in out["skipped"]


def test_cm_prefixed_component_line_dropped_and_counted_in_warnings():
    raw = _raw(
        headers=[{"cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1}],
        lines=[
            {"cbom_bid": "PKB-CM", "cbomid": "PK1", "cmaterialid": "MCM", "nitemnum": 1, "vrowno": "10"},
            {"cbom_bid": "PKB-CR", "cbomid": "PK1", "cmaterialid": "MCR", "nitemnum": 1, "vrowno": "20"},
        ],
        material_codes={"M1": "CF0001", "MCM": "CM0002", "MCR": "CR0001"},
    )
    out = transform(raw)
    # The CM component is gone from canonical lines...
    assert [ln["nc_source_pk"] for ln in out["lines"]] == ["PKB-CR"]
    # ...but visibly counted in warnings, not silently discarded, and NOT in
    # the unresolvable-code `skipped` bucket (it resolved fine, it's excluded).
    assert "PKB-CM" not in out["skipped"]
    assert any(
        w["nc_source_pk"] == "PKB-CM" and w["reason"] == "cm_component_excluded"
        and w["component_material_code"] == "CM0002"
        for w in out["warnings"]
    )


def test_cm_component_substitute_cascades_dropped_via_skipped():
    """A substitute row pointing at a CM-excluded line has no parent line to
    attach to — same orphan cascade as any other dropped line."""
    raw = _raw(
        headers=[{"cbomid": "PK1", "hcmaterialid": "M1", "hversion": "1.0", "fbillstatus": 1}],
        lines=[{"cbom_bid": "PKB-CM", "cbomid": "PK1", "cmaterialid": "MCM", "nitemnum": 1, "vrowno": "10"}],
        repl=[{"cbom_replaceid": "R1", "cbom_bid": "PKB-CM", "creplmaterialoid": "MCR", "vrowno": "10"}],
        material_codes={"M1": "CF0001", "MCM": "CM0002", "MCR": "CR0001"},
    )
    out = transform(raw)
    assert out["substitutes"] == []
    assert "R1" in out["skipped"]
