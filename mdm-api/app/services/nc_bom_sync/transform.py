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
  - `bom_lines`/`bom_substitutes` rows carry a synthetic
    `bom_nc_source_pk`/`bom_line_nc_source_pk` key (the parent's CBOMID /
    CBOM_BID) instead of a real `bom_id`/`bom_line_id` FK — this is a pure
    function with no DB access, so it cannot know the Postgres-assigned
    UUIDs. The sync service (canonical_sync.py) resolves these to real FKs
    at upsert time and pops the synthetic key before inserting.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

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
        boms.append({
            "product_material_code": code,
            "bom_type": bt,
            "version": _clean(h.get("hversion")),
            "factory_code": _clean(h.get("pk_org")),
            "status": _status(h.get("fbillstatus")),
            "effective_from": None,
            "effective_to": None,
            "yield_rate": _parse_ratio(h.get("hvchangerate")),
            "nc_source_pk": pk,
        })
        resolved_bom_pks.add(pk)

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
        bom_lines.append({
            "bom_nc_source_pk": bom_pk,
            "line_no": _line_no(ln.get("vrowno")),
            "component_material_code": code,
            "qty_per": _parse_qty(ln.get("nitemnum")),
            "uom": _resolve_uom(ln.get("cmeasureid"), uoms, line_pk, warnings, "uom"),
            "qty_per_secondary": _parse_qty_or_none(ln.get("nassitemnum")),
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
