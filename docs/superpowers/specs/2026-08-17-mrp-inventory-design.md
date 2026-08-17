# MRP Inventory — design

**Date:** 2026-08-17
**Module:** MRP (mrp-api, mrp frontend) + a data-layer change in epms-api's NC purchase sync
**Status:** approved in chat, ready for an implementation plan

## Goal

Three things a planner cannot do today:

1. **Search inventory.** The WMS lot mirror has 3,532 lots across 200
   materials and the only way to look at it is `GET /inventory/lots` filtered
   by an exact material code. No UI at all.
2. **See shelf life before it bites.** Warn at 180 / 60 / 30 days, and show
   what has already expired.
3. **See total supply per material** — on hand *plus* what is on order and
   not yet received, with the date it is expected.

## What the data actually supports

Everything below was measured against the dev database (a production
snapshot) and, for NC, against the live Oracle instance. The numbers are the
reason the design looks the way it does.

### Shelf life is available exactly where it is needed

`wms_inventory_lots.expiry_date` coverage, by material-code prefix:

| prefix | lots | with expiry |
|---|---|---|
| CR (raw materials) | 556 | **556** |
| CF (finished goods) | 517 | 517 |
| S0 (semi-finished) | 378 | 378 |
| CP (packaging) | 1,640 | 15 |
| ST / OT / DM / SA | 441 | 13 |

Packaging having no expiry is correct — it does not expire — not a data gap.
Aging therefore covers **lots that have an expiry date**, defaults its filter
to raw materials, and never presents "no expiry" as "expires today".

Aging buckets over the 556 raw-material lots, today:

| bucket | lots | qty |
|---|---|---|
| already expired | 90 | 32,020 |
| < 30 days | 11 | 1,069 |
| 30–60 days | 11 | 480 |
| 60–180 days | 97 | 76,185 |
| > 180 days | 347 | 249,172 |

**"Already expired" is its own bucket, not folded into "< 30 days".** 90 lots
holding 32 tonnes are already past date; a page that only counts down to zero
would never show them.

### The in-transit join is complete for raw materials and packaging

`purchase_orders.type = 1` is the raw-material/packaging type. Open lines
(`qty > received_qty`) on type-1 POs: **161 of 161 carry a material code** —
132 `CR*`, 29 `CP*`, none missing. (74 of those 161 are raw-milk lines that
the next section removes.) Of the 189 distinct material codes used
anywhere on PO lines, 187 resolve against the mdm materials master (the two
that do not, CP0128/CP0129, are retired codes).

The 893 open lines with *no* material code are all PO types 2/3/4/5/6
(consumables, spare parts, service, fixed assets, software). They have no
place in an MRP inventory view, so their absence costs nothing.

### ★ Raw-milk POs must be excluded or in-transit goes negative

`status = 'nc_milk'` is raw-milk procurement. Its receipt and stock-in happen
separately in NC and are never closed through UniOps' flag logic, so
`received_qty` is backfilled loosely from NC arrival records and routinely
**exceeds** the ordered quantity:

| material | name | received | qty − received |
|---|---|---|---|
| CR0180 | Lactalis-Raw Cows Milk | 10,035,150 | **−1,648,350** |
| CR0010 | Raw Cows Milk (12.2%) | 4,480,832 | **−1,525,432** |
| CR0177 | Lactalis Evaporated Skim Milk | 2,234,172 | +460,828 |

238 `nc_milk` lines net to **−2,695,743**. A naive
`type = 1 AND qty > received_qty` reports nonsense for the plant's
highest-volume materials.

Under the full rule in section B — type 1, `issued`/`partially_received`,
still owing — what is left is **87 lines across 71 materials, 902,779 in
transit**, and **0 of them carry a header `expected_delivery`**. Those 87 are
the 161 open type-1 lines above minus the 74 open `nc_milk` ones.

### ★ The expected arrival date exists in NC and our sync drops it

`purchase_orders.expected_delivery` is populated on **0** of the 161 open
raw-material lines (164 of 1,165 issued lines company-wide, none of them raw
material). The date does exist — in NC, at **line** level, in
`NCSC.PO_ORDER_B.DPLANARRVDATE`, on **4,890 of 4,890** approved order lines.
Verified against the ERP's own PO list:

| PO No. | shown in NC | `DPLANARRVDATE` |
|---|---|---|
| PO-001-2510-04 | 2026-02-02 | 2026-02-02 |
| PO-009-2603-01 | 2026-04-21 | 2026-04-21 |
| PO-010-2604-01 | 2026-05-05 | 2026-05-05 |
| PO-014-2602-01 | 2026-03-05 | 2026-03-05 |

The NC purchase sync's `PO_ORDER_B` query selects `pk_material`, quantities
and money columns, and no date at all. So the gap is in the sync, not in the
ERP. It is **line** level: PO-009-2603-01's two lines carry different dates,
which is why this cannot land on the existing header column.

## Design

### A. Data layer — bring `DPLANARRVDATE` across (epms-api)

New column `po_line_items.planned_arrival_date DATE NULL`. Three changes in
`app/services/nc_purchase_sync/`:

- `reader.py` — add `dplanarrvdate` to the `PO_ORDER_B` select.
- `transform.py` — the column is `CHAR` holding `'2026-02-02 10:01:25'`; the
  time part is the timestamp of whenever the value was last set and carries no
  business meaning. Take **the first 10 characters** and parse as a date.
  ★ No timezone conversion anywhere on this value — a date-only string put
  through a UTC conversion loses a day, and that bug has already shipped
  across this codebase once.
- `writer.py` — persist it on insert and update.

Existing rows are backfilled by running the sync's existing `full` mode; no
bespoke backfill script.

The header field `expected_delivery` is left alone. It is hand-entered on
UniOps-native POs and remains the fallback when a line has no NC date.

### B. In-transit definition — three rules, in one place

```
in_transit(material) = Σ (qty − received_qty) over po_line_items l
                       join purchase_orders po
  where po.type   = 1                                  -- raw material / packaging
    and po.status in ('issued', 'partially_received')   -- excludes nc_milk (see above)
    and l.qty > l.received_qty
    and l.material_id is not null
```

`approved` and `draft` are *ordered but not placed* and do not count as in
transit. `nc_milk` is excluded by name with the numbers above recorded at the
definition site, because the exclusion looks arbitrary without them.

Expected arrival per line: `planned_arrival_date` if present, else the PO
header's `expected_delivery`, else null — reported as "not stated", never
guessed. The material-level roll-up exposes the **earliest** expected arrival
among its open lines.

### C. mrp-api — one read-only mirror, three endpoints

**Mirror.** mrp-api and epms-api share one Postgres database, so the PO read
is a narrow read-only mirror model over `purchase_orders`/`po_line_items`
(precedent: `app/models/wms_inventory.py`), not an HTTP call to epms-api.
Aggregating 161 rows over HTTP is an N+1, and EPMS's PO endpoints carry
user-level department scoping that has no meaning for a service-to-service
aggregate.

The cost is that mrp-api becomes a consumer of EPMS's schema. Mitigation: a
guard test that asserts every mirrored column still exists with the expected
type in `information_schema.columns`. An EPMS rename then fails in CI instead
of silently zeroing the in-transit column — the failure mode this codebase has
hit three times with mirror models.

| endpoint | purpose |
|---|---|
| `GET /inventory/lots` | Lot search: free-text over material code, material name, lot no and supplier batch; filters for warehouse, mapped status, expiry window; sortable; paged. Extends the existing endpoint rather than replacing it — `app/services/net_requirement.py` and Phase 1C read it. |
| `GET /inventory/aging` | Bucket summary (expired / <30 / 30–60 / 60–180 / >180 days) plus the lots behind each bucket. Only lots with an expiry date; a `has_expiry=false` count is returned alongside so "excluded" never reads as "none". |
| `GET /inventory/materials` | Per material: on hand, available, allocated, on hold, expired qty, next expiry date, **in transit**, **earliest expected arrival**, open PO line count. |

All three gated `mrp.report.view`, consistent with the rest of the module.

Availability keeps the definition already in use: `available = qty − qty_onhold`
over lots with `mapped_status = 'available'`.

### D. Frontend — one `Inventory` nav item, three tabs

`/inventory` with tabs **Lots**, **Aging**, **Materials**. One material filter
shared across all three, preserved when switching tabs. Follows the existing
MRP page conventions: `@uniops/shell` `Button`, server-side paging with
`keepPreviousData`, an explicit error surface on every query (a silently empty
table is this project's most-repeated bug), English UI copy.

Aging arrives filtered to raw materials with the expired and <30-day buckets
expanded, because those are the two that require action.

### E. Out of scope, deliberately

- **No push notifications.** Page-only, per decision. The daily-reminder
  machinery exists and can be layered on later.
- **1C's net requirement still treats in-transit as zero.** This design
  creates the data source; changing the netting arithmetic would move numbers
  on already-released purchase suggestions and belongs in its own round.
- **No writes to inventory.** The WMS mirror is a full-extract snapshot
  replaced on every sync; anything written here would be destroyed by the next
  one.

## Testing

- `transform.py`: `'2026-02-02 10:01:25'` → `date(2026, 2, 2)`; null/blank/
  malformed → `None`; and an assertion that no timezone shift occurs.
- In-transit: `nc_milk` excluded (fixture reproducing CR0180's negative);
  `approved` excluded; partially-received line contributes only the remainder.
- Aging: boundary lots at exactly 30 / 60 / 180 days land in the documented
  bucket; a lot expiring today counts as expired, not `<30`; lots without an
  expiry date are counted separately and never bucketed.
- Mirror guard: every mirrored column exists in `information_schema` with the
  expected type.
- Endpoint filters applied in the database before paging (the trap fixed on
  the Outlooks list the same week).
- Permission gate: each new endpoint 403s a role without `mrp.report.view`.

## Migrations

- epms-api: one migration adding `po_line_items.planned_arrival_date`, chained
  onto the current head `ag09_receipt_amounts_nullable`.
- mrp-api: **none.** Every read is over existing tables.
