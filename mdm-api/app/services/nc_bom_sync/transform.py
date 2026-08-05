"""Pure transform: raw NC BOM extract -> canonical boms/bom_lines/bom_substitutes
row dicts, keyed by nc_source_pk for upsert.

`transform(raw)` consumes exactly the shape `reader.fetch_nc_bom()` returns
(`{"headers": [...], "lines": [...], "repl": [...], "material_codes": {pk:
code}}`) — no DB access, no NC access, so it's trivially unit-testable and
callable on either a live extract or a fixture. Field mapping follows
docs/superpowers/specs/2026-08-03-nc-bom-survey.md's "字段 -> Task 5 规范化
模型映射表", not the task-5-brief's guessed field names (pk_bom/
pk_invmandoc/hstate/basenum/wastagerate) — the survey postdates and
supersedes the brief.

Key decisions baked in here (each justified in the survey, cited by section):
  - `bom_type` is derived from the parent material CODE PREFIX (CS->milling,
    CW->drymix, CF->packaging, S->packaging), never from any NC column —
    FBOMTYPE is not a reliable layer discriminator (survey §3). `S*` is a
    second, business-confirmed spelling of "finished good" alongside `CF*`
    (legacy coding) — S0093's real BOM is structurally identical to CF0092's
    (dry-mix powder + packaging items), verified against live NC data
    2026-08-04 (PATCH 1). An unrecognized prefix maps to 'unknown' and is
    recorded in `warnings` (nc_source_pk + resolved code), never silently
    dropped from the sync.
  - `uom`/`uom_secondary` are resolved from BD_BOM_B's CMEASUREID/
    CASSMEASUREID measure-doc PKs via the `uoms` (BD_MEASDOC pk->code)
    lookup the reader now provides — NOT stored as the raw PK anymore
    (PATCH 2). A present-but-unresolvable PK leaves the field null and is
    recorded in `warnings` (never a crash, never silently dropped from the
    line). A blank/absent PK (no secondary unit on this line) resolves to
    None with no warning — that's the normal case for any line that isn't a
    two-unit finished-good line.
  - `qty_per_secondary` (<- NASSITEMNUM) is the assistant-unit quantity S*
    finished-good BOM lines carry alongside the main-unit `qty_per` (<-
    NITEMNUM) — e.g. main unit KG, secondary unit PIECES, business-confirmed
    conversion lives in the material master (PATCH 3). Populated only when
    NC actually supplies a value; left None (not 0) when absent, since most
    non-finished-good lines carry only a single unit and 0 would misread as
    "zero pieces" instead of "not a two-unit line."
  - `_UOM_NORMALIZE` collapses NC's `EA`/`PIECES` unit codes to one
    canonical `EA` (business-confirmed: both mean "个" for packaging
    materials — PATCH 4) so downstream unit math never has to reconcile two
    codes that mean the same thing. Applied to both `uom` and
    `uom_secondary` after the BD_MEASDOC PK is resolved to a code.
  - CM (standardized milk) is deprecated ~2 years ago and excluded from the
    canonical sync as dead-data hygiene, not a live-planning change (PATCH
    5, business-confirmed: the CM-touching BOMs are stale v1.0 rows for 25
    legacy CF products with zero production in the last 12 months; no live
    production order touches CM at any BOM depth). Two distinct exclusions:
    (a) a BOM HEADER whose PARENT material is CM-prefixed is skipped
    entirely, same bucket as any other unresolved header (`skipped`) — its
    lines never get a `boms` row to attach to, same cascade as an
    unresolved parent code; (b) a BOM LINE whose COMPONENT material is
    CM-prefixed is dropped from `bom_lines` but recorded in `warnings`
    (distinct from `skipped` — this is a visible exclusion of a resolvable
    row, not an unresolvable one) rather than silently vanishing. Neither
    path attempts to explode through/around a CM component — there is no
    live BOM that needs it (see the patch's own note not to add a
    fallback).
  - `status`: FBILLSTATUS 1->'approved', -1->'draft', any other/unparsable
    value->'inactive' (only 1/-1 were observed in the full-table survey scan
    — survey §6).
  - `yield_rate` <- HVCHANGERATE, a "numerator/denominator" string (e.g.
    '1000/1'); parsed as Decimal(num)/Decimal(den). Blank/unparsable ->
    Decimal('1') (a no-op multiplier), never a crash.
  - `scrap_rate` always defaults to Decimal('0') for every line. NC's
    candidate loss columns (NBFIXSHRINKNUM/NBFIXSHRINKASTNUM/NDISSIPATIONUM)
    are NULL across all 10169 sampled BD_BOM_B rows in this NC instance
    (survey §7) — this is a real absence of source data, not a missing
    mapping. Do NOT wire a percentage-to-decimal conversion from those
    columns without new evidence they carry values.
  - Effective dating lives at the LINE level (BD_BOM_B.CBEGINPERIOD/
    CENDPERIOD), not the header — BD_BOM has no date columns at all (survey
    §8). `bom_lines` rows carry effective_from/effective_to; `boms` rows do
    not (see app/models/bom.py's docstring).
  - NC's empty-value placeholder is the literal string '~', not SQL NULL
    (survey §6) — `_clean()` normalizes '~' (and blank/whitespace strings)
    to None everywhere a field could carry it.
  - A header whose parent material code can't be resolved via
    `material_codes` is skipped entirely (its `nc_source_pk` lands in
    `skipped`); its component lines are dropped too rather than becoming
    orphan `bom_lines` with no `boms` row to attach to — the dropped line's
    own `nc_source_pk` also lands in `skipped` (not silently discarded), so a
    caller can see the true fan-out of one unresolved header without digging
    through logs. Same cascade for a line skipped for an unresolvable
    component code, and for a substitute whose parent line was dropped (its
    `nc_source_pk` lands in `skipped` too).
  - NC's soft-delete flag DR (1 = logically deleted, never physically
    removed — the survey's own sample header has DR=1) is filtered out at
    the reader's SQL level (`nvl(dr,0)=0`); `_is_deleted()` is a
    defense-in-depth re-check inside `transform()` itself for callers/
    fixtures that bypass the reader. A DR<>0 row's `nc_source_pk` lands in
    `skipped`.
  - PATCH 6 (2026-08-04, CRITICAL defect fix): `bom_lines.qty_per`/
    `qty_per_secondary` are now NORMALIZED TO PER-ONE-UNIT-OF-PARENT —
    `qty_per = NITEMNUM / HNPARENTNUM`, `qty_per_secondary = NASSITEMNUM /
    HNASSPARENTNUM` — not `NITEMNUM`/`NASSITEMNUM` taken as-is. The survey's
    own mapping recommendation ("样本中两者恒相等，直接取 NITEMNUM 作为
    qty_per", survey §BD_BOM_B) missed that `NITEMNUM` is a whole-BATCH
    quantity, not a per-unit one — the batch size lives on the HEADER
    (`BD_BOM.HNPARENTNUM`/`HNASSPARENTNUM`, "头级基本产出数量", survey line
    ~55), never captured before this patch. Symptom: BOM explosion
    (app/services/bom_explode.py) multiplied raw batch-scaled quantities
    down the cascade and produced quantities wrong by orders of magnitude
    (one real smoke test: ~113 million units of a component for 1 unit of
    finished good). Verified against live NC65 (2026-08-04, 911 approved+
    draft headers spot-checked): `HNPARENTNUM` is NOT always 1 (distribution
    includes 1000, 1, 4.8, 42, 3, ...); S0093's header has HNPARENTNUM=420,
    HNASSPARENTNUM=100 — its CW0001 (dry-mix powder) line's NITEMNUM=420
    normalizes to `qty_per=1.0` (1 kg powder per kg finished, correct), and
    its CP0115-1 (tin) line's NITEMNUM=610 normalizes to `qty_per≈1.4524`
    (≈688g/tin, matching the real product spec — previously this line
    reported a *raw* 610, off by 420x). `HNASSPARENTNUM` is the assistant-
    unit counterpart used for `qty_per_secondary`'s divisor (not
    `HNPARENTNUM` again) because the two can legitimately differ (S0093:
    420 vs 100). A live full-table check found ZERO headers with a NULL or
    non-positive `HNPARENTNUM`/`HNASSPARENTNUM`, so `_resolve_divisor`'s
    None/<=0 -> fallback-to-1-with-a-warning path is defensive, not a real
    observed case — but it's still guarded, never a crash or a silent
    divide-by-zero. Results are `.quantize()`d to `_QTY_QUANT` (10 decimal
    places, matching migration 0015's widened `Numeric(24,10)` columns) —
    6 decimal places would badly round a real, legitimate small ratio (e.g.
    S0093's CP0132 line: `1/420 = 0.00238095238...` needs more than 6dp to
    not lose most of its significant digits).
  - `yield_rate` (<- `HVCHANGERATE`) is `HNPARENTNUM/HNASSPARENTNUM`
    EXPRESSED AS A RATIO (survey: 头级用量换算比 "输出/输入",
    survey line 54-55) — a unit-of-measure conversion factor between the
    header's own primary and secondary UOM, NOT a production-yield/loss
    term, and NOT the same *value* as either divisor individually (S0093:
    yield_rate=4.2 equals neither HNPARENTNUM=420 nor HNASSPARENTNUM=100 —
    it's their quotient). See bom_explode.py's docstring for why this ratio
    must NOT also be divided into the explosion's accumulated quantity (it
    is dimensionally a UOM-conversion factor, not a yield/loss multiplier,
    and it is NOT always 1 — one live combination is HNPARENTNUM=1000/
    HNASSPARENTNUM=1 -> yield_rate=1000 — so dividing by it again would
    reintroduce a version of this exact bug at up to 1000x). This assumed
    identity (`yield_rate == HNPARENTNUM/HNASSPARENTNUM`) was verified by
    hand against all 46 distinct live `(HVCHANGERATE, HNPARENTNUM,
    HNASSPARENTNUM)` combinations on 2026-08-04 with zero exceptions, but a
    one-time hand check is not an enforced guarantee — `_check_yield_rate_
    invariant()` below re-verifies it on every sync (within
    `_YIELD_RATE_TOLERANCE`) and emits a counted `warnings` entry naming
    the BOM if NC ever starts populating it differently, so a silent
    divergence surfaces immediately instead of quietly mis-planning.
  - `bom_lines`/`bom_substitutes` rows carry a synthetic
    `bom_nc_source_pk`/`bom_line_nc_source_pk` key (the parent's CBOMID /
    CBOM_BID) instead of a real `bom_id`/`bom_line_id` FK — this is a pure
    function with no DB access, so it cannot know the Postgres-assigned
    UUIDs. The sync service (canonical_sync.py) resolves these to real FKs
    at upsert time and pops the synthetic key before inserting.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_BOM_TYPE_PREFIXES_2 = {"CS": "milling", "CW": "drymix", "CF": "packaging"}
# S-prefixed materials (e.g. S0093) are a second, current-day spelling of
# "finished good" alongside the legacy CF coding — business-owner-confirmed,
# verified against live NC data 2026-08-04 (S0093's BOM = CW dry-mix powder
# + CP packaging items, structurally identical to CF0092's). Checked as a
# single-character prefix so it doesn't collide with the 2-char CS/CW/CF/CM
# table above (all of which start with 'C', never 'S').
_BOM_TYPE_PREFIX_1 = {"S": "packaging"}

# Business rule (owner-confirmed 2026-08-03/04): NC's `EA` and `PIECES` unit
# codes both mean "个" (a single packaging piece) — treat them as ONE
# canonical unit so downstream unit math (e.g. MRP quantity conversion)
# never has to reconcile two codes that denote the same thing. Canonical
# code = 'EA'. Applied after a BD_MEASDOC PK resolves to a code, to both the
# primary (`uom`) and secondary (`uom_secondary`) fields.
_UOM_NORMALIZE = {"PIECES": "EA"}

_CM_PREFIX = "CM"  # standardized milk, deprecated ~2 years ago — PATCH 5

# Scale for `qty_per`/`qty_per_secondary` after the PATCH 6 batch-size
# normalization — matches migration 0015's widened `Numeric(24,10)` columns.
# 10 fractional digits comfortably represents NC's real repeating-decimal
# ratios (e.g. 1/420 = 0.0023809523809...) without the precision loss a
# narrower 6dp scale would cause on a genuinely small, legitimate ratio.
_QTY_QUANT = Decimal("1e-10")

# Relative tolerance for `_check_yield_rate_invariant`'s
# yield_rate == HNPARENTNUM/HNASSPARENTNUM check (floored against 1 near
# zero so a tiny expected ratio doesn't demand implausible float-style
# precision from a value that's already parsed from a "num/den" string).
_YIELD_RATE_TOLERANCE = Decimal("0.000001")


def _clean(v):
    """NC's '~' empty-value placeholder (and blank strings) -> None."""
    if v is None:
        return None
    if isinstance(v, str) and v.strip() in ("", "~"):
        return None
    return v


def _bom_type(material_code: str) -> str:
    code = material_code.upper()
    if code[:2] in _BOM_TYPE_PREFIXES_2:
        return _BOM_TYPE_PREFIXES_2[code[:2]]
    if code[:1] in _BOM_TYPE_PREFIX_1:
        return _BOM_TYPE_PREFIX_1[code[:1]]
    return "unknown"


def _is_cm(material_code: str | None) -> bool:
    return bool(material_code) and material_code.upper()[:2] == _CM_PREFIX


def _normalize_uom(code: str | None) -> str | None:
    if code is None:
        return None
    c = code.strip().upper()
    return _UOM_NORMALIZE.get(c, c)


def _resolve_uom(pk_measdoc, uoms: dict, nc_source_pk: str, warnings: list, field: str):
    """BD_MEASDOC pk -> normalized unit code. A blank/absent pk resolves to
    None with no warning (the normal case: not every line carries a
    secondary unit). A present-but-unresolvable pk resolves to None AND is
    recorded in `warnings` — never a crash, never silently dropped."""
    pk = _clean(pk_measdoc)
    if not pk:
        return None
    code = uoms.get(pk)
    if code is None:
        warnings.append({
            "nc_source_pk": nc_source_pk, "reason": "unresolved_uom_pk",
            "field": field, "measdoc_pk": pk,
        })
        return None
    return _normalize_uom(code)


def _status(fbillstatus) -> str:
    try:
        v = int(fbillstatus)
    except (TypeError, ValueError):
        return "inactive"
    if v == 1:
        return "approved"
    if v == -1:
        return "draft"
    return "inactive"


def _parse_ratio(raw) -> Decimal:
    """'1000/1' -> Decimal('1000'); '1/1' -> Decimal('1'); blank/unparsable
    -> Decimal('1') (a safe no-op multiplier, never a crash)."""
    v = _clean(raw)
    if not v:
        return Decimal("1")
    try:
        v = str(v)
        if "/" in v:
            num_s, den_s = v.split("/", 1)
            num, den = Decimal(num_s), Decimal(den_s)
            return num / den if den else Decimal("1")
        return Decimal(v)
    except (InvalidOperation, ValueError, ZeroDivisionError):
        return Decimal("1")


def _parse_qty(raw) -> Decimal:
    if raw is None:
        return Decimal("0")
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return Decimal("0")


def _parse_qty_or_none(raw) -> Decimal | None:
    """Like `_parse_qty`, but absent/unparsable -> None, not 0 — used for
    `qty_per_secondary`, where None means "this line has no assistant-unit
    quantity" (the common case) and 0 would misread as "zero pieces."""
    v = _clean(raw)
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _resolve_divisor(
    raw_value: Decimal | None, fallback: Decimal, nc_source_pk: str, warnings: list, field: str,
) -> Decimal:
    """HNPARENTNUM/HNASSPARENTNUM ("头级基本产出数量" — survey line ~55): the
    BOM header's own batch-output quantity that BD_BOM_B's NITEMNUM/
    NASSITEMNUM are scaled AGAINST, not a per-unit quantity by themselves —
    see PATCH 6 in this module's docstring for the full defect writeup.

    None/<=0/unparsable -> `fallback`, AND a warning: a live NC65 full-table
    spot-check (2026-08-04, 911 approved+draft headers) found ZERO rows with
    a NULL or non-positive HNPARENTNUM/HNASSPARENTNUM, so landing here is a
    genuine data anomaly worth surfacing to whoever runs the sync, not
    routine defensive noise — but it still never crashes or silently
    divides by zero either way."""
    if raw_value is None or raw_value <= 0:
        warnings.append({
            "nc_source_pk": nc_source_pk, "reason": "invalid_batch_divisor", "field": field,
            "raw": None if raw_value is None else str(raw_value),
        })
        return fallback
    return raw_value


def _check_yield_rate_invariant(
    yield_rate: Decimal, hnparentnum: Decimal | None, hnassparentnum: Decimal | None,
    nc_source_pk: str, product_material_code: str, warnings: list,
) -> None:
    """Runtime guard for the assumption `bom_explode.py` relies on to NOT
    divide by `yield_rate` (see this module's docstring and bom_explode.py's
    "Accumulation formula" section): that `yield_rate` (<- HVCHANGERATE) is
    always exactly `HNPARENTNUM/HNASSPARENTNUM`. Verified by hand against
    911 live headers on 2026-08-04 with zero exceptions — but a one-time
    hand check is not an enforced guarantee, so this re-checks it on every
    sync and surfaces a `warnings` entry (never a crash — this is an
    informational anomaly report, not a value used in any downstream math)
    if NC ever starts populating `HVCHANGERATE` differently, so a silent
    divergence is caught immediately instead of quietly mis-planning.

    Only checks when BOTH divisors are present and the denominator is
    non-zero — a missing/invalid divisor is already reported by
    `_resolve_divisor` (a distinct, line-triggered concern) wherever a line
    actually needs it; this function's job is purely the cross-field
    consistency of the header's own metadata, not divisor availability."""
    if hnparentnum is None or hnassparentnum is None or hnassparentnum == 0:
        return
    expected = hnparentnum / hnassparentnum
    tolerance = _YIELD_RATE_TOLERANCE * max(abs(expected), Decimal("1"))
    if abs(expected - yield_rate) > tolerance:
        warnings.append({
            "nc_source_pk": nc_source_pk, "reason": "yield_rate_batch_ratio_mismatch",
            "product_material_code": product_material_code,
            "yield_rate": str(yield_rate), "expected": str(expected),
            "hnparentnum": str(hnparentnum), "hnassparentnum": str(hnassparentnum),
        })


def _parse_date(raw) -> date | None:
    """'YYYY-MM-DD HH24:MI:SS' or 'YYYY-MM-DD' -> date; blank/'~' -> None."""
    v = _clean(raw)
    if not v:
        return None
    v = str(v)
    date_part = v.split(" ", 1)[0]
    try:
        return datetime.strptime(date_part, "%Y-%m-%d").date()
    except ValueError:
        return None


def _line_no(raw) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return 0


def _is_deleted(rec: dict) -> bool:
    """NC's soft-delete flag: DR=1 means "logically deleted" (never
    physically removed — the survey's own sample header has DR=1). The
    reader already filters `nvl(dr,0)=0` at the SQL level, but transform()
    is also called directly against fixtures/tests and, if the nc_bom* raw
    mirror ever holds stale DR<>0 rows synced before the reader filter
    existed, against those too — so this is a second, defense-in-depth
    check, not a duplicate of the SQL filter."""
    dr = rec.get("dr")
    if dr is None:
        return False
    try:
        return Decimal(str(dr)) != 0
    except (InvalidOperation, ValueError):
        return False


def transform(raw: dict) -> dict:
    """raw: {"headers": [...], "lines": [...], "repl": [...],
    "material_codes": {pk: code}} -> {"boms": [...], "lines": [...],
    "substitutes": [...], "skipped": [nc_source_pk, ...], "warnings": [...]}."""
    material_codes: dict = raw.get("material_codes") or {}
    uoms: dict = raw.get("uoms") or {}
    headers = raw.get("headers") or []
    lines = raw.get("lines") or []
    repl = raw.get("repl") or []

    boms: list[dict] = []
    bom_lines: list[dict] = []
    substitutes: list[dict] = []
    skipped: list[str] = []
    warnings: list[dict] = []

    resolved_bom_pks: set[str] = set()
    # PATCH 6: raw header dicts for resolved headers, keyed by pk, so the
    # line loop below can read HNPARENTNUM/HNASSPARENTNUM lazily (only for
    # headers that actually have a surviving line) instead of resolving a
    # divisor for every header up front — a header with zero lines (or whose
    # only lines all get dropped for other reasons, e.g. CM exclusion) never
    # needs a divisor and must never generate a spurious warning for one.
    header_by_pk: dict[str, dict] = {}
    # Memoized resolved divisors, populated lazily the first time a line
    # under that header actually needs one — so a header with N lines
    # generates at most one "invalid_batch_divisor" warning per field, not N.
    primary_divisors: dict[str, Decimal] = {}
    secondary_divisors: dict[str, Decimal] = {}

    for h in headers:
        pk = h.get("cbomid")
        if _is_deleted(h):
            if pk:
                skipped.append(pk)
            continue
        code = material_codes.get(h.get("hcmaterialid"))
        if not pk or not code:
            if pk:
                skipped.append(pk)
            continue
        if _is_cm(code):
            # PATCH 5(a): a BOM whose PARENT is CM-prefixed (standardized
            # milk, deprecated ~2 years ago) is dead data — 55 such headers
            # observed live, all stale v1.0 rows for legacy CF products with
            # zero production in the last 12 months. Same bucket as any
            # other unresolved header (`skipped`); its lines cascade-drop
            # below via the existing "parent not resolved" path — no
            # separate CM check needed there.
            skipped.append(pk)
            continue
        bt = _bom_type(code)
        if bt == "unknown":
            warnings.append({
                "nc_source_pk": pk, "reason": "unknown_bom_type_prefix",
                "product_material_code": code,
            })
        yield_rate = _parse_ratio(h.get("hvchangerate"))
        hnparentnum_val = _parse_qty_or_none(h.get("hnparentnum"))
        # Cross-checked against HNASSPARENTNUM here (header-level, cheap,
        # independent of whether any line actually needs a divisor) — see
        # _check_yield_rate_invariant's docstring and this module's
        # docstring for why this must hold for bom_explode.py's no-divide
        # decision to stay correct.
        _check_yield_rate_invariant(
            yield_rate, hnparentnum_val, _parse_qty_or_none(h.get("hnassparentnum")),
            pk, code, warnings,
        )
        boms.append({
            "product_material_code": code,
            "bom_type": bt,
            "version": _clean(h.get("hversion")),
            "factory_code": _clean(h.get("pk_org")),
            "status": _status(h.get("fbillstatus")),
            "effective_from": None,
            "effective_to": None,
            "yield_rate": yield_rate,
            # Raw HNPARENTNUM, kept for traceability (planners/Phase 1C need
            # to see the source batch size) — distinct from the *divisor*
            # the line loop below resolves from it, which is always a safe
            # positive Decimal even when this is None (PATCH 6).
            "batch_output_qty": hnparentnum_val,
            "nc_source_pk": pk,
        })
        resolved_bom_pks.add(pk)
        header_by_pk[pk] = h

    resolved_line_pks: set[str] = set()

    for ln in lines:
        bom_pk = ln.get("cbomid")
        line_pk = ln.get("cbom_bid")
        if not line_pk:
            continue  # no PK to upsert/key on -> can't be synced, same as the raw mirror's own rule
        if _is_deleted(ln):
            skipped.append(line_pk)
            continue
        if bom_pk not in resolved_bom_pks:
            skipped.append(line_pk)  # parent header unresolved/skipped -> no orphan line
            continue
        code = material_codes.get(ln.get("cmaterialid"))
        if not code:
            if line_pk:
                skipped.append(line_pk)
            continue
        if _is_cm(code):
            # PATCH 5(b): a BOM line whose COMPONENT is CM-prefixed
            # (standardized milk, deprecated) is dropped from canonical
            # bom_lines — but unlike an unresolvable code, this component
            # DID resolve; it's being deliberately excluded as dead-data
            # hygiene, so it must be visible (warnings), not folded into the
            # same `skipped` bucket as a genuine resolution failure. No
            # fallback/explosion through CM — the business confirms no live
            # BOM depth needs it.
            warnings.append({
                "nc_source_pk": line_pk, "reason": "cm_component_excluded",
                "component_material_code": code,
            })
            continue
        # PATCH 6: NITEMNUM/NASSITEMNUM are whole-BATCH quantities, not
        # per-unit ones — normalize against this line's parent header's
        # HNPARENTNUM/HNASSPARENTNUM. Divisors are resolved lazily (only
        # once actually needed) and memoized per header/field, so a header
        # with several lines warns at most once per field, and a header
        # whose lines never carry a secondary unit never touches
        # HNASSPARENTNUM (or warns about it) at all.
        if bom_pk not in primary_divisors:
            primary_divisors[bom_pk] = _resolve_divisor(
                _parse_qty_or_none(header_by_pk[bom_pk].get("hnparentnum")),
                Decimal("1"), bom_pk, warnings, "hnparentnum",
            )
        primary_divisor = primary_divisors[bom_pk]
        nitemnum_val = _parse_qty(ln.get("nitemnum"))
        qty_per = (nitemnum_val / primary_divisor).quantize(_QTY_QUANT, rounding=ROUND_HALF_UP)

        nassitemnum_val = _parse_qty_or_none(ln.get("nassitemnum"))
        if nassitemnum_val is not None:
            if bom_pk not in secondary_divisors:
                secondary_divisors[bom_pk] = _resolve_divisor(
                    _parse_qty_or_none(header_by_pk[bom_pk].get("hnassparentnum")),
                    primary_divisor, bom_pk, warnings, "hnassparentnum",
                )
            qty_per_secondary = (nassitemnum_val / secondary_divisors[bom_pk]).quantize(
                _QTY_QUANT, rounding=ROUND_HALF_UP
            )
        else:
            qty_per_secondary = None
        bom_lines.append({
            "bom_nc_source_pk": bom_pk,
            "line_no": _line_no(ln.get("vrowno")),
            "component_material_code": code,
            "qty_per": qty_per,
            "uom": _resolve_uom(ln.get("cmeasureid"), uoms, line_pk, warnings, "uom"),
            "qty_per_secondary": qty_per_secondary,
            "uom_secondary": _resolve_uom(
                ln.get("cassmeasureid"), uoms, line_pk, warnings, "uom_secondary"
            ),
            "scrap_rate": Decimal("0"),  # NC has no loss data in this instance — survey §7
            "effective_from": _parse_date(ln.get("cbeginperiod")),
            "effective_to": _parse_date(ln.get("cendperiod")),
            "nc_source_pk": line_pk,
        })
        if line_pk:
            resolved_line_pks.add(line_pk)

    for r in repl:
        line_pk = r.get("cbom_bid")
        sub_pk = r.get("cbom_replaceid")
        if not sub_pk:
            continue  # no PK to upsert/key on -> can't be synced, same as the raw mirror's own rule
        if _is_deleted(r):
            skipped.append(sub_pk)
            continue
        if line_pk not in resolved_line_pks:
            skipped.append(sub_pk)  # parent line unresolved/dropped -> no orphan substitute
            continue
        code = material_codes.get(r.get("creplmaterialoid"))
        if not code:
            if sub_pk:
                skipped.append(sub_pk)
            continue
        substitutes.append({
            "bom_line_nc_source_pk": line_pk,
            "substitute_material_code": code,
            "priority": _line_no(r.get("vrowno")),
            "mode": "suggest",
            "nc_source_pk": sub_pk,
        })

    return {
        "boms": boms, "lines": bom_lines, "substitutes": substitutes,
        "skipped": skipped, "warnings": warnings,
    }
