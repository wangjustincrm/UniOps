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
    CW->drymix, CF->packaging), never from any NC column — FBOMTYPE is not a
    reliable layer discriminator (survey §3). An unrecognized prefix maps to
    'unknown' and is recorded in `warnings` (nc_source_pk + resolved code),
    never silently dropped from the sync.
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

_BOM_TYPE_PREFIXES = {"CS": "milling", "CW": "drymix", "CF": "packaging"}


def _clean(v):
    """NC's '~' empty-value placeholder (and blank strings) -> None."""
    if v is None:
        return None
    if isinstance(v, str) and v.strip() in ("", "~"):
        return None
    return v


def _bom_type(material_code: str) -> str:
    prefix = material_code[:2].upper()
    return _BOM_TYPE_PREFIXES.get(prefix, "unknown")


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
        bom_lines.append({
            "bom_nc_source_pk": bom_pk,
            "line_no": _line_no(ln.get("vrowno")),
            "component_material_code": code,
            "qty_per": _parse_qty(ln.get("nitemnum")),
            "uom": _clean(ln.get("cmeasureid")),
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
