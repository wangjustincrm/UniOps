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
        material_codes={"M1": "CF0086", "M2": "CM0040"},
    )
    out = transform(raw)

    b = out["boms"][0]
    assert b["product_material_code"] == "CF0086" and b["version"] == "1.0"
    assert b["nc_source_pk"] == "PK1"
    assert b["status"] == "approved"

    ln = out["lines"][0]
    assert ln["component_material_code"] == "CM0040"
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
        material_codes={"M1": "CF0001", "M2": "CM0001"},
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
        material_codes={"M1": "CF0001", "M2": "CM0001"},
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
        material_codes={"M1": "CF0001", "M2": "CM0001", "M3": "CM0002"},
    )
    out = transform(raw)
    assert len(out["substitutes"]) == 1
    sub = out["substitutes"][0]
    assert sub["substitute_material_code"] == "CM0002"
    assert sub["priority"] == 10
    assert sub["mode"] == "suggest"
    assert sub["nc_source_pk"] == "R1"
