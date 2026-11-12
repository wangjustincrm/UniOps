# Sales — Radiant inventory (3PL stock ledger)

Status: design, 2026-11-12 (product owner agreed the approach: the stock held at Radiant is computed in Sales).
Related: Order Bridge design `2026-10-23-order-bridge-design.md` (open items OB-OI-02 inventory history, OB-OI-30 returns and inventory; permission `sales.inventory.adjust` reserved), Inventory module `2026-10-14-inventory-module-design.md` (plant stock from WMS + NC; Radiant is not in it).

## 1. Goal

Show, at any moment and for any past date, the exact stock of each item **by lot and expiry** at Radiant (CLN02), and what is on its way in or out:

| Figure | Meaning |
|---|---|
| On hand at Radiant | received (944) − shipped (945) + returns received (RA 944) ± adjustments |
| Allocated | on 940s sent and not yet answered by a 945 |
| Available | on hand − allocated |
| In transit to Radiant | on 943s sent, not yet received (item level: a 943 carries no lot) |
| In transit to customers | shipped (945), delivery not confirmed |
| Delivered | delivery confirmed: shipped − returned |
| Returns on their way back | Returned EA recorded, not yet received back at Radiant |

Expiry warnings use each partner's `min_shelf_life_days`.

## 2. Principle: a ledger, never a stored balance

One append-only table of **stock moves**; every figure is a sum over it. A move is never edited; a correction is a new move with a reason. Each move comes from exactly one source record (a 944 row, a 945 row, a delivery confirmation, an adjustment), so the ledger can be rebuilt from the files at any time (OB-OI-02) and every number on screen can be traced to its file.

```
sales_stock_moves
  id, at (the event's own time: 944 receipt date, 945 ship date, confirmation date)
  item_code, lot (null where the source has none), expiry (null where unknown)
  qty_ea (> 0), from_place, to_place          -- places: PLANT, TO_RADIANT, RADIANT, TO_CUSTOMER, CUSTOMER, RETURNING, WRITTEN_OFF
  source_type, source_id                      -- receipt_row / shipment_row / delivery_line / return / adjustment / opening
  transfer_id, order_id, document_id          -- for links and filters
  reason (adjustments, openings), created_*
  unique (source_type, source_id, from_place) -- a source moves once: re-reading a file never doubles stock
```

Allocation is not a move (nothing left the shelf): it is derived from open 940s and their lines.

## 3. Events → moves

| Event (already in Sales) | Move |
|---|---|
| 943 put (transfer sent) | PLANT → TO_RADIANT, per transfer line (no lot) |
| 944 applied (transfer receipt) | TO_RADIANT → RADIANT per receipt row, with lot; the item-level in-transit is cleared by the same quantity. A discrepancy (00104, 00106) shows as stock in transit that never arrived plus an unexpected item received — visible, not hidden |
| 945 applied (packing list) | RADIANT → TO_CUSTOMER per shipment row, with lot and expiry |
| Delivery confirmed | TO_CUSTOMER → CUSTOMER (shipped − returned) and TO_CUSTOMER → RETURNING (returned EA), per line |
| RA 944 (return received at Radiant) | RETURNING (or, without a recorded return, outside) → RADIANT, with lot |
| Adjustment (a person, `sales.inventory.adjust`, reason required) | RADIANT ↔ WRITTEN_OFF (damage, destroyed returns, count differences) |
| Opening balance (once) | → RADIANT per item and lot, from Radiant's stock report at the cut-off date |

Expiry: a 945 row carries it (G62*36); a 944 row does not — taken from the lot's expiry as known from its 945s or the opening report; unknown stays null and is listed as such.

## 4. Starting point

The full history cannot be replayed exactly: old 945s were partly Zoho's. So: **one opening balance per item and lot at a cut-off date**, from Radiant's own stock report; only events after that date make moves. Events before it are left as they are (their files stay with their orders and transfers).

## 5. Reconciliation with Radiant

Radiant's count is the reference. If Radiant can send an **846** (inventory inquiry/advice) or a periodic stock file (CSV/Excel), Sales reads it like any other file and compares per item and lot: equal → nothing; different → a task listing the differences, and a person decides (an adjustment with a reason, or a question to Radiant). Without such a file, the same comparison runs on a report uploaded by hand.

## 6. Screens

- **Inventory** (new, under Operations): one row per item and lot — on hand, allocated, available, expiry, days left; totals per item; filters (item, lot, expiring within N days); group by item. Columns for in transit (to Radiant, to customers) and returns on the way.
- **Stock card** (record sheet of an item or lot): its moves in time order, each with its source (transfer, order, file) one click away — the same list printable / downloadable.
- **Adjust** (action, `sales.inventory.adjust`): quantity, lot, reason; shown in the card like any move.
- The Lots page (recall) gains the lot's current stock at Radiant.

## 7. Build order

1. Ledger table + move writers in the existing steps (944, 945, confirmation, return) + backfill command from the files since the cut-off. Tests: every event writes its move once; a re-read file writes nothing.
2. Inventory list + stock card + adjust.
3. Opening balance import (Radiant's report) and the reconciliation (846 or uploaded file).
4. Expiry warnings and the dashboard card.

## 8. Decisions needed

1. Cut-off date for the opening balance, and Radiant's stock report at that date (per item and lot).
2. Can Radiant send an 846 or a regular stock file, and how often?
3. A returned item: does it always come back to Radiant (an RA 944), or is it sometimes destroyed / kept by the customer? (Then a write-off move with a reason.)
4. Are the RA 944s already received (RA CRM4750, RA CRM8102, RA CRM8961) before or after the cut-off?
