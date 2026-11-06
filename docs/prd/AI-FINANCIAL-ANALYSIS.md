# AI Financial Analysis — Product Requirements Document

- **Version:** 1.0
- **Date:** 2026-10-16
- **Status:** Released — Phases 1–5 live in Finance since 2026-10-13; Phase 6 (AI Explain) not started
- **Scope:** A standalone, read-only cost and margin analysis module inside Finance that traces cost along the full chain — raw milk components → raw materials → semi-finished goods → finished goods → selling price — using NC data.
- **Source:** Module design (2026-10-09, with product-owner decisions of 2026-10-09 and 2026-10-13), release notes and hand-off notes, and the delivered implementation.

### What changed in this version

- First PRD for this module; it previously existed only as an internal design note.
- Records the delivered scope (Phases 1–5: mirror and tie-out, material trace and anomalies with diagnosis, gross margin and cost chain, cost composition, batch cost, standard cost proposal) and the remaining Phase 6 (AI Explain).
- Records the booking-order rule added on 2026-10-13 (an issue booked before the same month's receipt is a correction, not an anomaly).

---

## 1. Background

In October 2026 Finance found that pasteurized skim milk (CR0059) carried a **negative** cost into downstream products. Manual investigation took more than a dozen database queries and deep knowledge of NC document flows. The root cause:

1. July milk-component receipts were booked at a provisional price and fully consumed in July.
2. The August settlement at a lower price produced a variance of about -61,900, which NC could only post to August, entirely onto CR0059.
3. August CR0059 output was small, so its unit cost became **-1.2112/kg**; the negative value was carried into September.
4. NC's month-end "zero quantity, non-zero value" tail adjustment (-46,588.85) was then pushed onto the last consumer, semi-finished powder CS0080, and propagated further to CW0005 and the finished product S0104.

This module turns that investigation into a product: deterministic calculations that show **what is wrong, why, and where it propagates**, with every conclusion backed by NC figures and document numbers.

## 2. Product decisions

| # | Decision | Date |
| --- | --- | --- |
| D1 | Standalone module inside Finance; **not** part of the AI assistant chat. | 2026-10-09 |
| D2 | **No tasks and no notifications of any kind.** Users open the pages themselves. | 2026-10-09 |
| D3 | Scope is the full cost chain: milk components → raw material receipt prices → semi-finished → finished goods → selling price. | 2026-10-09 |
| D4 | Calendar year 2026 only for now; earlier years once the results are proven. | 2026-10-09 |
| D5 | Revenue basis: CAD functional currency, excluding tax. | 2026-10-09 |
| D6 | Add finished-goods **batch cost** (a capability NC does not have). | 2026-10-09 |
| D7 | Add a **standard cost model proposal**, in preparation for a possible move to standard costing. | 2026-10-09 |
| D8 | Usage, batch and standard cost are based on **material consumption** only, never on line-side picking (which is a warehouse move). | 2026-10-09 |
| D9 | The module must **analyse and summarise, then show the supporting data** — showing only data adds nothing over NC. | 2026-10-09 |
| D10 | Adjustment vouchers shown in traces include **unposted** vouchers, using the same basis as Budget's "include unposted". | 2026-10-09 |
| D11 | An issue booked before the same month's receipt and corrected by NC's month-end adjustment is a booking-order correction, shown as information, not as an anomaly. | 2026-10-13 |

## 3. Positioning and constraints

- **NC is the source of truth** (see FINANCE.md). The module reads NC only, never writes back to NC and keeps no separate ledger. Unit cost, product cost and cost of sales are NC's figures.
- **Analysis figures are labelled.** The only derived figures (cost composition, batch actual cost, standard cost) are marked as analysis, and each layer must reconcile to NC's total; any difference is shown as "Unattributable", never spread.
- **Nothing is dropped silently.** Rows filtered out, unmatched or not calculable are counted and visible.
- **AI only interprets.** Applications may call **only Claude Haiku**, through the single API key shared company-wide (company rule; a stronger model would exhaust the shared quota and disable document recognition across UniOps). The model receives only structured results computed by the back end; it does not query data, calculate, or introduce new numbers. The module's core value must not depend on AI.
- **No tasks, no notifications** (D2). This is a deliberate exception to the UniOps convention that exceptions create Task Inbox items.

## 4. Users and permissions

| ID | Requirement |
| --- | --- |
| FR-FA-01 | A dedicated permission, **Finance analysis view**, governs every page and data endpoint. It is **not** granted by general Finance access, because unit costs and margins are sensitive. |
| FR-FA-02 | By default the permission is granted to the Finance Manager, Finance Director and System Administrator roles; it is honoured whether held as a primary or an additional role. |
| FR-FA-03 | Users without the permission do not see the menu group, and a direct link or API call is refused. |
| FR-FA-04 | "Sync now", a full reload and configuration changes (thresholds) are restricted to System Administrators. A full reload requires a typed confirmation. |
| FR-FA-05 | The Portal treats the analysis permission as a Finance entry permission, so holders reach Finance from the Portal (PORTAL.md). |

## 5. Data sources

The module keeps a read-only mirror of the NC65 inventory-accounting, cost-management and sales-invoice tables for 2026 (about 20,000 rows):

| NC data | Content |
| --- | --- |
| Inventory detail ledger | Every receipt, issue and adjustment with amounts |
| Inventory month-end balances | Month-end quantity and value (written only when NC closes the month) |
| Cost objects | Products costed in NC |
| Product cost header and lines | Output, total and unit cost by element and input material |
| Material collection | Material costs collected per cost object, including issue adjustments |
| Work-in-progress cost | WIP lines |
| Cost elements | Raw milk, pasteurized milk, auxiliary materials, packaging, overhead, labour, utilities, stoppage |
| Sales invoices | Invoice header and lines (approved invoices only) |
| Accounting-platform relations | Links from inventory/cost documents to vouchers and their posting status |
| Bills of materials | Default NC BOM (already mirrored for MRP; see MRP.md) |

| ID | Requirement |
| --- | --- |
| FR-FA-10 | Synchronise incrementally on NC's change timestamp, header and lines separately; keep NC logical deletions in the mirror and exclude them from analysis. |
| FR-FA-11 | Run on a schedule (default every 240 minutes, under the global NC sync switch) and on demand ("Sync now"). Return "not configured" and hide the feature when no NC connection exists. |
| FR-FA-12 | After each sync, tie out NC against the mirror by period and document type (row counts and amounts). Show sync time and tie-out status at the top of every page ("Ties out with NC" or a red warning). No task is raised (D2). |
| FR-FA-13 | Limit data to 2026 through a single lower bound so that earlier years can be enabled later by changing one setting (D4). |
| FR-FA-14 | Treat a month as closed only when NC has written its month-end balance; unclosed months are shown but flagged, and checks that depend on closing are skipped. |

## 6. Features

### 6.1 Material Cost Trace

| ID | Requirement |
| --- | --- |
| FR-FA-20 | For a material and period range, show a monthly ledger: opening, receipts by category (purchase receipts split into provisional / reversal / settlement, production receipts, other receipts, receipt adjustments), issues by category (consumption, cost of sales, other issues, issue adjustments), closing, and unit cost. |
| FR-FA-21 | Reconcile each closed month's rolled-forward closing to NC's month-end balance; differences are anomalies. |
| FR-FA-22 | Show transaction detail with document numbers, provisional/settlement pairing and adjustments, including linked vouchers and their posting state (posted or unposted, D10). |
| FR-FA-23 | Net warehouse transfers (line-side picking/returns, finished-goods transfers, transfers to Amazon, cost-carrying stock adjustments, other business moves) **by document**, not by monthly totals; unpaired single legs are anomalies. Transfer pairs are configuration, not code. |
| FR-FA-24 | Consumption counts only material-consumption transaction types (production material consumption, production consumption, component consumption). Unknown consumption types are flagged as "unclassified", never silently included or dropped. |

### 6.2 Cost Anomalies and diagnosis

| ID | Rule | Default threshold |
| --- | --- | --- |
| FR-FA-30 / A1 | Production receipt unit cost ≤ 0 | — |
| FR-FA-30 / A2 | Unit cost moves versus the previous producing period | ±30% |
| FR-FA-30 / A3 | Closing quantity zero but value non-zero, or negative closing unit cost | value > 1 |
| FR-FA-30 / A4 | Provisional-to-settlement variance as a share of the material's consumption in the period | > 20% and > 1,000 |
| FR-FA-30 / A5 | Unpaired transfer leg | — |
| FR-FA-30 / A6 | Single large issue adjustment | > 5,000 |
| FR-FA-30 / A7 | Invoice month differs from cost-of-sales period; revenue without cost or cost without revenue | — |
| FR-FA-30 / A8 | Mirror does not tie out, or ledger roll-forward differs from NC's month-end balance | — |
| FR-FA-30 / A9 | Unattributable share in cost composition | > 1% |

| ID | Requirement |
| --- | --- |
| FR-FA-31 | Thresholds are configurable by a System Administrator; missing values fall back to the defaults above. |
| FR-FA-32 | Each anomaly drills down to document numbers and shows downstream propagation: which cost objects consumed the material, how much, and which finished product it reached (including the following month, since production regularly spans month-end). |
| FR-FA-33 | **Diagnosis:** for an anomalous product-month, break product cost into consumption, conversion cost, settlement variance, month-end tail and amounts rolled in from earlier periods; explain each in plain language built only from mirrored figures (provisional→settlement after the goods were used up; negative upstream input; tail-adjustment source; comparison with output in the month the goods were consumed and what both months should have been); trace inherited values recursively to their root. Conclusions appear first, supporting data below. |
| FR-FA-34 | Recognised root-cause patterns include: settlement variance landing on a low-output month; stock returned to inventory at a wrong value; conversion cost dominating (NC spreads conversion cost evenly per kg across products in a month); and negative cost inherited from upstream. |
| FR-FA-35 | Booking-order corrections (D11) are shown as grey "Info" findings, excluded from A6 and from the root-cause summary. |

### 6.3 Gross Margin

| ID | Requirement |
| --- | --- |
| FR-FA-40 | Product × month matrix of quantity, revenue, unit price, cost of sales, unit cost, gross margin and margin %, with anomalous cells highlighted. |
| FR-FA-41 | Revenue is approved sales-invoice net amount in CAD excluding tax (D5); cost of sales is matched to invoices line by line and assigned to the invoice month. |
| FR-FA-42 | By-products (cream, animal feed) are included and labelled as by-products. |

### 6.4 Cost Chain and composition

| ID | Requirement |
| --- | --- |
| FR-FA-50 | For a finished product and period, show the cost tree down to milk, built from NC's actual input relationships (not the BOM), per period. Each node shows unit cost and change; anomalies on a node can be opened. |
| FR-FA-51 | Match each self-made input to the production month whose unit cost best matches the actual issue cost (within the last three producing months). |
| FR-FA-52 | "What a unit is made of": expand self-made inputs **by quantity** into material buckets (raw milk, auxiliary, milk powder, packaging), conversion buckets (overhead, labour, utilities, stoppage) and adjustment buckets (settlement, tail, roll-in, issue price difference). Each layer reconciles to NC; the remainder is "Unattributable" (A9). |

### 6.5 Batch Cost

| ID | Requirement |
| --- | --- |
| FR-FA-60 | For a finished product and period, list each batch with output and two costs: **NC-basis batch cost** (order consumption at NC issue price plus a share of the month's conversion cost by output) and **batch actual cost** (self-made inputs valued recursively at the upstream batch's own actual cost; accounting variances shown on a separate line). The difference is the accounting impact on that batch. |
| FR-FA-61 | Show the batch genealogy (finished batch → dry blend → semi-finished → pasteurized milk → milk-component month), up to five levels. Milk-component consumption carries no production order, so genealogy stops at pasteurized milk and continues on a monthly basis; this is labelled. |
| FR-FA-62 | Receipts without a production order fall back to the monthly average and are labelled and counted; batches on the same receipt document are assigned to that document's order. |
| FR-FA-63 | The sum of NC-basis batch costs for a month equals NC's product cost. |

### 6.6 Standard Cost (proposal only)

| ID | Requirement |
| --- | --- |
| FR-FA-70 | For each finished and semi-finished product, propose a standard cost card: standard usage per unit (median of actual batch usage, outliers removed), purchase prices (settled or invoiced receipts, not provisional), self-made inputs rolled up from their own standards, and a conversion rate per unit excluding stoppage. |
| FR-FA-71 | Compare proposed usage with the default NC BOM (BOM quantities are per unit) and list BOM revision candidates. |
| FR-FA-72 | Replay 2026 month by month: actual − standard = usage + purchase price + self-made input cost + conversion + accounting adjustments, with zero residual. Demonstrate that under standard costing the provisional-to-settlement variance would sit in purchase price variance instead of product cost. |
| FR-FA-73 | Export the cards as CSV (opens in Excel). The proposal is never posted anywhere. The conversion-rate basis is marked "to be decided by Finance". |

### 6.7 AI Explain (Phase 6, not started)

| ID | Requirement |
| --- | --- |
| FR-FA-80 | An "Explain" button on Material Trace, anomaly, batch and standard-cost views produces a plain-language causal explanation, only when clicked. |
| FR-FA-81 | The model receives only a structured fact pack computed by the back end (ledger, anomaly hits, pairings, adjustments, downstream consumption, document numbers). |
| FR-FA-82 | Every number in the output must appear in the fact pack; otherwise the explanation is not shown. |
| FR-FA-83 | Model is Claude Haiku only, via the shared company key, fixed (not configurable); token usage is recorded; identical fact packs are cached. |
| FR-FA-84 | Without a key or when quota is exhausted, the button is disabled and all other pages work normally. |

## 7. Pages

All pages are in a Finance menu group **AI Financial Analysis**, English UI only, each showing sync and tie-out status.

| Page | Purpose |
| --- | --- |
| Gross Margin | Product × month margin matrix |
| Cost Anomalies | All anomaly rules, filterable by period and rule, with diagnosis and drill-down |
| Cost Chain | Cost tree to milk, with composition ("What a unit is made of") |
| Batch Cost | Batches with NC-basis and actual cost, genealogy |
| Standard Cost | Proposed standard cost cards, BOM revision candidates, variance replay, CSV export |
| Material Cost Trace | Monthly ledger and transactions per material |

## 8. Out of scope

- Data before 2026 (D4).
- Any task, notification or email (D2).
- Writing back to NC or adjusting cost in UniOps.
- Posting standard costs; budget-versus-cost comparison (possible later link to BUDGET.md).
- Free-form chat questions (D1; see the AI assistant in PORTAL.md).

## 9. Delivery phases

| Phase | Content | Status |
| --- | --- | --- |
| P1 | NC mirror, sync, tie-out; Material Cost Trace; anomalies A1–A6, A8; diagnosis; voucher posting state | Released 2026-10-13 |
| P2 | Gross Margin; Cost Chain; A7 | Released 2026-10-13 |
| P3 | Cost composition; A9 | Released 2026-10-13 |
| P4 | Batch cost and genealogy | Released 2026-10-13 |
| P5 | Standard cost proposal and variance replay | Released 2026-10-13 |
| P6 | AI Explain (Haiku) | Not started |

Acceptance was demonstrated on real NC data: all mirrored tables tie out; the January–August ledger roll-forward equals NC month-end balances; the CR0059 case reproduces exactly (August -47,735.79 / -1.2112 per kg; September tail -46,588.85 landing on CS0080); S0104's September batch shows NC 5.9151 versus actual 17.94 per unit, with the difference inherited from CR0059.

## 10. Related documents

- FINANCE.md — host application; NC-as-source-of-truth positioning; unposted-voucher basis.
- BUDGET.md — "include unposted vouchers" basis shared with voucher states here.
- MRP.md — NC BOM mirror used for standard usage comparison.
- INVENTORY.md — inventory quantities from the other NC ledgers.
- PORTAL.md — Portal entry, permissions, AI assistant (separate from this module).

## 11. Open Items

| ID | Item | Status |
| --- | --- | --- |
| FA-OI-01 | Standard conversion-rate basis (per kg, labour hours or normal capacity) — to be decided by Finance; pages currently note this. | Open — Finance |
| FA-OI-02 | CR0234 June 2026 month-end shows quantity 0 and value 2,505.67 — ask Finance for the cause. | Open — Finance |
| FA-OI-03 | Default A6 threshold (5,000) appears loose (34 findings after booking-order corrections were excluded); confirm thresholds A2, A4 and A6. | Open — Finance |
| FA-OI-04 | Roles beyond Finance Manager, Finance Director and System Administrator (for example Finance business partners, production managers). | Open |
| FA-OI-05 | Batch conversion-cost allocation basis: output share (current) or labour/machine hours if a reliable source exists. | Open |
| FA-OI-06 | Phase 6 AI Explain: confirm it is still wanted and schedule it. | Open |
| FA-OI-07 | Extend to years before 2026 once results are accepted (D4). | Deferred |
| FA-OI-08 | Excel export currently CSV; confirm whether a formatted workbook is required. | Open |
