# MRP — Product Requirements Document

**Version:** 2.1
**Date:** 2026-10-19
**Status:** Current. Describes the MRP module as built on main. Requirements from the original plan that were never built are marked **Not implemented** and listed under Open Items.
**Scope:** Single-plant milk-powder MRP: sales forecast, master production schedule (MPS), multi-level BOM explosion and purchase suggestions, using read-only WMS and NC65 data.

**What changed in version 2.1** (2026-10-19)

- Every list sorts by clicking a column header, ascending then descending (column sorting rule in EPMS.md §3.20 (UI-SORT-001…007)): purchase suggestions, supply parameters, capacity rules, week exceptions, forecast outlooks and consignment stock. Purchase suggestions still open sorted by order date (FR-PUR-08).
- Consignment stock pages on the server; it used to show only the first 100 entries.

**What changed in this version**

- Translated from the original Chinese PRD (V1.0, 2026-07-09). This is the first version kept in the repository.
- The whole document now follows the system as built, not the original plan. The original twelve functional requirements (FR-001 to FR-012) are kept in Section 5.1 with their build status. Detailed requirements for what was built are in Sections 5.2 to 5.13.
- New sections cover features that were added after the original PRD:
  - continuous sales forecast with frozen "outlook" snapshots;
  - intent (placeholder) products;
  - weekly MPS with minimum lot size, frozen zone and plan versioning ("plan in force");
  - purchase suggestions (Phase 1C) and the Supply Parameters screen;
  - loss rates;
  - finished-goods classification;
  - consignment stock counted by supplier batch;
  - scheduled WMS sync.
- Data rules that turned out to matter are now written down:
  - BOM quantities are stored per unit of parent, not per batch;
  - the packaging loss rate must stay 0;
  - available stock has one fixed definition.
- The WMS Inventory screens moved to the Inventory module (see INVENTORY.md) on 2026-10-14. MRP now links out to them, but its backend still serves their data.
- Requirements that were planned but never built now carry a status and are collected in Open Items. These include the exception message centre, the planner workbench, simulation, the report centre, safety stock, in-transit netting and conversion to purchase requisitions.

---

## 1. Overview

### 1.1 Purpose

MRP is the UniOps planning module for the milk-powder plant. It links the sales forecast, the production plan, BOM/recipe structure, warehouse stock and supplier parameters into one planning chain. The output is a weekly production plan and a dated list of purchase suggestions.

The module is a **lightweight supply-chain guidance tool**, not an execution system. It does not run the shop floor (no MES). It does not write anything back to WMS or NC. Its purchase output is a list for buyers. It does not create purchase orders or requisitions.

### 1.2 Background (unchanged from V1.0)

- The plant plans many layers of material at once: raw and auxiliary materials, packaging, semi-finished powder and finished goods. Spreadsheet planning does not hold up at this scale.
- Quality status and shelf life decide whether stock is really usable. If planning ignores them, stock looks available on paper when it is not.
- Process manufacturing involves yield, loss, substitutes, rework and packaging conversion. The system has to account for these.
- Planning, purchasing, warehouse, quality and production need one shared set of numbers.

**Finding during build: there is no forward demand signal in any existing system.** The NC65 forecast and MPS tables were never used (0 rows). NC sales orders and production orders carry no future dates. WMS outbound orders are shipment history. The planner's manual forecast grid is therefore the **only** demand input. This is the design, not a stop-gap. Demand for semi-finished powder, raw materials and packaging is always derived by BOM explosion and is never entered directly.

### 1.3 Scope

| Category | As built |
| --- | --- |
| In scope (built) | Continuous sales forecast grid; frozen outlook snapshots; intent products; consignment stock counts; read-only WMS stock mirror; BOM explorer; capacity rules and week calendar; weekly MPS engine with lead time, shelf-life gate, minimum lot and frozen zone; plan versioning with a single plan in force; plan-to-plan comparison; Excel export of plans; supply parameters maintenance; purchase suggestions with loss rates, stock netting, lead-time back-scheduling and MOQ/multiple rounding. |
| In scope, not built | Exception message centre, planner workbench, simulation/what-if, report centre, safety stock and other lot-size strategies, holiday calendar, QC buffer time, in-transit and open-PO netting, conversion of suggestions to purchase requisitions, parameter approval workflow, interface monitoring page, event-triggered recalculation. See Open Items. |
| Out of scope | Finite-capacity detailed scheduling (APS), MES/shop-floor integration, supplier portal, multi-plant planning and transfers, AI optimisation. |
| Organisation | One milk-powder plant. Covers raw and auxiliary materials, packaging, semi-finished powder and finished goods. |

### 1.4 Success indicators (targets from V1.0)

These are the original targets. The system does not measure any of them yet, because there is no report centre (see Open Items).

| Indicator | Target |
| --- | --- |
| Plan generation time | Routine run within 15 minutes |
| Shortage detection rate | ≥ 95% |
| Suggestion adoption rate | ≥ 80% |
| Manual reconciliation time | Down 50% or more |

---

## 2. Users and system boundaries

### 2.1 Roles

Access is set by permission keys granted in the Portal Access Control matrix (see PORTAL.md). No role receives MRP keys by default; only System Admin has all of them.

| Role | Main job | Permission keys used |
| --- | --- | --- |
| Planner | Maintains the forecast and consignment counts, generates and adjusts plans | `mrp.report.view`, `mrp.demand.write`, `mrp.run.execute` |
| Plan approver | Releases a plan, switches the plan in force | `mrp.proposal.confirm` |
| Buyer | Runs purchase suggestions, marks lines ordered/ignored, exports them | `mrp.report.view`, `mrp.run.execute` |
| Planning administrator | Capacity rules, week calendar, loss rates, supply parameters, WMS refresh | `mrp.param.write` (and see FR-SUP-06) |
| BOM / master-data administrator | Refreshes BOMs from NC; maintains supply parameters | `mdm.bom.write` or `data_maintenance` |
| Viewer | Read-only access to all MRP screens | `mrp.report.view` |

The keys `mrp.proposal.export` and `mrp.exception.handle` exist in the permission catalogue but no screen or endpoint checks them (see Open Items).

### 2.2 System boundaries (as built)

| System | Direction | Data | Notes |
| --- | --- | --- | --- |
| Sales / order systems | — | — | **No integration.** Demand is entered by hand in the forecast grid (Section 1.2). |
| Flux WMS | Inbound, read-only | Lots, supplier batch, NC QC batch, quality status, quantities, on-hold quantity, expiry, production date, unit, locations | Copied into a local mirror on a schedule (default every 5 minutes). MRP never writes to WMS: no reservations and no allocations. |
| NC65 (via MDM) | Inbound, read-only | BOMs and BOM lines (with versions and effective periods), materials, units of measure | Synced into the shared master-data service. BOM data cannot be edited in UniOps. |
| Master-data service (MDM) | Inbound | Materials, BOM explosion, where-used, supply parameters (material × supplier), shelf life | Supply parameters are maintained from the MRP screens but stored in MDM. |
| Purchasing (EPMS / NC POs) | — | — | **No integration in MRP planning.** Suggestions are not turned into requisitions, and open POs are not netted (see Open Items). The Inventory module shows open PO lines per material (see INVENTORY.md). |
| MES | — | — | None. Production feedback is not captured. |
| QMS | Indirect | — | Quality results reach MRP only through the WMS quality status. |
| Inventory module | Shares data | WMS mirror | The Inventory module's WMS screens read MRP's WMS mirror (see INVENTORY.md). |
| AI assistant | Reads | Plan in force, current forecast version, newest suggestion run | The UniOps assistant is available inside MRP. Its answers cover only the plan in force, never superseded runs, so totals are not counted twice. It can also answer "how much of product X can we make from stock". That answer uses a multi-level BOM explosion and the same available-stock definition as MRP (FR-STK-02). |

---

## 3. End-to-end flow (as built)

The original plan was a daily rolling run triggered by events. What was built is **planner-driven and on demand**: each step is started by a person, and only the WMS mirror refreshes itself.

1. **Forecast.** The planner keeps one continuous forecast grid of finished goods by month, in kg. They edit it directly or paste it from Excel.
2. **Outlook.** The planner freezes a window of the grid (default 18 months from an anchor month) into an immutable, confirmed **outlook** snapshot.
3. **Plan generation.** The planner generates an MPS run from an outlook. The engine:
   - nets each product's monthly forecast against available stock (WMS plus consignment);
   - spreads the net requirement into weeks under capacity rules, minimum lot sizes, production lead time and the shelf-life limit;
   - copies the frozen months from the plan currently in force.
4. **Review.** The planner reviews the weekly matrix and can move, resize or lock individual lines, recalculate, compare with the plan in force, and export to Excel.
5. **Release.** An approver releases the run. It becomes the single **plan in force**, and its weekly production quantities become the demand set for material planning.
6. **Purchase suggestions.** A buyer runs purchase suggestions against the plan in force. The system:
   - explodes the BOMs to purchased components;
   - adds loss;
   - nets each component's stock week by week;
   - works back from the need week by the supplier lead time to an order date;
   - rounds up to the MOQ and order multiple.
7. **Purchasing.** The buyer works through the list sorted by order date, marks lines Ordered or Ignored, and exports to Excel. Orders themselves are placed outside MRP.

Not built: the exception message centre, automatic re-runs after WMS status changes, and conversion of suggestions into requisitions.

### 3.1 Key business objects

| Object | Meaning | Role in MRP |
| --- | --- | --- |
| Demand series | Living forecast per finished good × month (kg) | The only editable demand input |
| Outlook (forecast version) | Frozen, confirmed copy of a window of the series | Input to an MPS run |
| MPS run / lines | Weekly production plan per product | Draft → released → superseded; exactly one run is in force |
| MRP demand | The weekly production quantities of the plan in force | Input to purchase suggestions |
| Purchase run / lines | Dated purchase suggestions per component and need week | A buyer's work list; kept, never recomputed |
| Capacity rule / week exception | Weekly limits and per-week overrides | Constrain the MPS |
| Planning parameter | Plant-wide settings (week mode, week start, frozen months, loss rates, WMS sync interval) | Shape every run; snapshotted onto runs where relevant |
| Consignment count | Weekly hand count of finished goods held at the consignment warehouse | Part of available stock |

---

## 4. Master data and planning parameters

### 4.1 Master data (as used)

| Object | Source | Rules as built |
| --- | --- | --- |
| Materials | ERP, via MDM | Code, name, spec, base unit, ERP MES type. Most richer attributes (item type, product family, procurement type) are empty or constant in the source, so they are not used. |
| Finished-goods classification | Derived | A material is a finished good when its ERP MES type is `3`. The by-products **CF00AF** (animal feed) and **CF00WT** (waste powder) are excluded. Code prefixes are **not** used: prefixes would miss numeric-coded finished goods and would wrongly include rework items (`CF****-R`). Product pickers show finished goods only, unless the screen asks for all materials (for example where-used). |
| Purchased vs made | Derived | In BOM explosion, a component that has its own BOM is **made**. It passes its quantity down and never becomes a purchase requirement. This holds even when its code looks like a raw material. The Supply Parameters picker offers only `CR*` (raw and auxiliary) and `CP*` (packaging) materials. |
| BOM / recipe | NC65, via MDM, read-only | Approved versions only. For each product, the highest version with a line effective on the query date wins; ties are broken deterministically. **Quantity per unit = NC batch line quantity ÷ NC batch output quantity.** This division is done once, during sync. Anything downstream must never divide again: doing so is wrong by orders of magnitude (a real case was 113,337,630 against the correct 0.2699). The original batch quantity and batch size are stored for display only. Substitute lines are mirrored but not used in planning. |
| Packaging conversion | BOM structure | Finished good → dry-mix powder (optional level) → powder → raw materials, plus packaging (cans, scoops, labels, cartons) on the packaging level. Explosion handles every level (verified up to 4 in production data). |
| Supply parameters | Hand-maintained in MRP, stored in MDM | Per material × supplier: lead time (days), MOQ, order multiple, primary flag. At most one primary per material. |
| Shelf life | ERP, via MDM | In months, per finished good. A product with no shelf life on record is never built ahead, and the plan summary names it. |
| Stock attributes | WMS mirror | Quality status mapped to available or hold; expiry; supplier batch; NC QC batch; on-hold quantity. |
| Planning unit | Fixed | All planning quantities for finished goods are in **KG**. The forecast grid can show tonnes, but values are stored in kg. Capacity rules are kg only. Units are checked: a non-kg quantity in the forecast or consignment stock is refused rather than added up. |

### 4.2 Planning parameters (as built)

| Parameter | Default | Range | Where set | Notes |
| --- | --- | --- | --- | --- |
| Week calendar mode | ISO, Thursday rule | ISO-Thursday / ISO-first-day / month-fixed | Capacity Rules → Planning Calendar | Decides which month a week that spans two months belongs to. Snapshotted on each run. |
| Week starts on | Monday | Monday … Sunday | Capacity Rules → Planning Calendar | The plant's week runs Saturday to Friday. Changing it also moves existing week exceptions, in the same transaction. If two exceptions would land in the same week, the change is refused. Snapshotted on each run. |
| Production lead (weeks) | 4 | 0–52 | Production Plan → Generate | How far ahead of the demand month production is placed. Snapshotted on the run. |
| Frozen months | 3 | 0–24 | **No screen** (API only) | See FR-MPS-09 and Open Items. |
| Shelf-life safety margin | 1/3 | — | Default; no screen (can be overridden per run through the API) | Production may be pulled earlier only while the weeks early stay within shelf life × (1 − margin). Days are counted from real calendar dates; months are not approximated. |
| Raw-material loss rate | 0 | 0–1 (fraction) | Capacity Rules → Loss Rates | Applies to every consumed component that is not packaging. |
| Packaging loss rate | 0 | 0–1 (fraction) | Capacity Rules → Loss Rates | **Must stay 0** until NC's packaging BOMs are made exact, because they already include loss (FR-CAP-07). |
| Minimum lot size | none | kg | Capacity Rules (per product; plant-wide default) | Rule type "minimum output quantity", either plant-wide or per product. |
| Weekly capacity | none | kg / SKU count | Capacity Rules + Week Exceptions | Maximum output per week, maximum number of SKUs per week, minimum output per week. |
| WMS sync interval (minutes) | 5 | 0–1440 (0 = off) | Portal → Admin → WMS Sync | The scheduler re-reads the value on every tick. |

V1.0 parameters that were **not built**: safety stock, reorder point, fixed/max lot size, fill-to-max and period-grouping strategies, QC buffer time, receiving/production holiday calendars, per-warehouse overrides, and parameter versioning with approval.

---

## 5. Functional requirements

### 5.1 Original requirement set (V1.0) and status

| ID | Area | Priority | Requirement (V1.0) | Status |
| --- | --- | --- | --- | --- |
| FR-001 | Demand management | P0 | Import forecast, orders and MPS; freeze and version them | **Partly built.** Continuous forecast grid with Excel paste, frozen outlook snapshots and a full change log. Customer orders are not imported and there is no forecast-vs-order consumption (no source system has them). |
| FR-002 | MRP engine | P0 | Net-requirement run with period-by-period balance | **Built (weekly).** Monthly netting of finished goods, a weekly MPS, then a weekly component explosion with stock netting. There is no single run that shows a projected available balance per material across all levels. |
| FR-003 | Planner workbench | P0 | One screen for suggestions, exceptions and stock risk, with batch actions | **Not implemented.** Work is spread over the Production Plan and Purchase Suggestions screens. |
| FR-004 | Purchase suggestions | P0 | Suggestions by supply strategy; convert to purchase requisition | **Built, except conversion.** Line status (pending / ordered / ignored) is in place for a future requisition link. |
| FR-005 | Internal supply reference | P0 | Production references from recipe, yield and lot rules | **Partly built.** The MPS is the internal supply plan for finished goods. Semi-finished powder is not planned as its own supply; it is passed through to its components. |
| FR-006 | Stock / shelf-life collaboration | P0 | Batch, shelf life and quality status drive availability | **Partly built.** Hold and expired stock are excluded. There is no minimum remaining shelf life per order type or customer. |
| FR-007 | Stock status collaboration | P0 | WMS status changes affect the plan in real time | **Partly built.** The mirror refreshes every 5 minutes, but plans are not recalculated automatically. |
| FR-008 | Exception messages | P0 | Expedite / delay / cancel / shortage / safety-stock messages | **Not implemented** as managed objects. Flags appear on plan and suggestion lines instead (Sections 5.10 and 5.13). |
| FR-009 | Simulation | P1 | Local simulation and comparison | **Not implemented.** Draft runs and plan comparison partly cover it. |
| FR-010 | Reports | P0 | Supply/demand balance, shortages, stock health, execution rate | **Not implemented** as a report centre. Excel exports exist for plans, suggestions, BOMs and outlooks. Stock aging is in the Inventory module. |
| FR-011 | Permissions and audit | P0 | Role / plant / planner-group access and audit log | **Partly built.** Permission keys per action, a forecast change log, and generated-by / updated-by on runs and parameters. There is no planner-group scoping and no general audit log. |
| FR-012 | Interface monitoring | P1 | Upstream freshness, retries, alerts | **Partly built.** WMS sync status, last success, interval and manual refresh are in Portal Admin. BOM sync status is on BOM Explorer. There are no alerts. |

### 5.2 Access and navigation

| ID | Requirement | Status |
| --- | --- | --- |
| FR-ACC-01 | The MRP tile in the Portal and the MRP entry in the side bar appear only to users with `mrp.report.view` (or System Admin). | Built |
| FR-ACC-02 | Every read screen requires `mrp.report.view`. Writes require the key for that action: forecast and consignment `mrp.demand.write`; plan generate/recalculate/adjust and purchase runs `mrp.run.execute`; plan release and switching the plan in force `mrp.proposal.confirm`; capacity, calendar, loss rates and WMS refresh `mrp.param.write`. | Built |
| FR-ACC-03 | BOM data is commercially sensitive. BOM explode, where-used and effective-BOM reads require `mrp.report.view`, not just a login. BOM refresh requires `mdm.bom.write` or `data_maintenance`. | Built |
| FR-ACC-04 | The module side bar contains: Sales Forecast, Consignment Stock, Inventory ↗ (opens the Inventory module in a new tab and passes the session), BOM Explorer, Capacity Rules, Production Plan, Supply Parameters, Purchase Suggestions. | Built |
| FR-ACC-05 | Planner-group and warehouse scoping of data. | Not implemented |

### 5.3 Sales forecast (continuous grid)

| ID | Requirement | Status |
| --- | --- | --- |
| FR-FC-01 | One living forecast table holds finished good × calendar month. It is not versioned; only non-zero cells are stored. | Built |
| FR-FC-02 | The grid shows the current month −3 to +24 by default and highlights the 18-month window an outlook would freeze. | Built |
| FR-FC-03 | Past months are read-only, enforced both on screen and on the server. Month values must be shaped YYYY-MM. | Built |
| FR-FC-04 | Cells are edited in place and autosaved; only changed cells are sent. One invalid cell rejects the whole batch, so nothing is half-saved. | Built |
| FR-FC-05 | A block can be pasted from Excel, and pasting can create rows for product codes that are new to the grid. Undo/redo covers whole paste blocks. A very large paste asks for confirmation first. | Built |
| FR-FC-06 | Values are stored in KG. A display toggle switches between kg and tonnes (default tonnes, remembered per browser). Pasted and typed values are scaled back to kg. | Built |
| FR-FC-07 | Every changed cell writes a change-log entry (old → new, who, when). The editor's name is stored at write time. A per-cell history popover shows the entries. | Built |
| FR-FC-08 | Products are added with a picker that lists finished goods only. A product with no BOM is still allowed, with a "No BOM" badge. | Built |
| FR-FC-09 | Removing a row that was just added must not break the grid. | Built |
| FR-FC-10 | Excel file import with a validation report. | Retired; replaced by paste. A template download still exists for outlook versions. |
| FR-FC-11 | Import of customer orders or promotions, and a policy for consuming or adding forecast against orders. | Not implemented (no source data) |
| FR-FC-12 | Daily and weekly forecast granularity. | Not implemented; the forecast is monthly by design and only the MPS is weekly. |

### 5.4 Intent products

| ID | Requirement | Status |
| --- | --- | --- |
| FR-INT-01 | A planner can forecast a product that has no ERP code yet. It is stored under an `INTENT-` placeholder code with a name. | Built |
| FR-INT-02 | Binding an intent product to a real material code renames its forecast rows in one transaction and writes an audit entry with the author. A new outlook must be generated afterwards; the screen says so. | Built |
| FR-INT-03 | A wrongly created intent product can be dropped, after confirmation. | Built |
| FR-INT-04 | Outlook snapshots record which lines were intent lines. MPS generation skips them and lists them in the run summary. Net requirements and purchase suggestions never include intent products. | Built |

### 5.5 Outlook snapshots (forecast versions)

| ID | Requirement | Status |
| --- | --- | --- |
| FR-OUT-01 | "Generate Outlook" freezes [anchor month, anchor + horizon) of the series into an immutable, confirmed snapshot. Defaults: anchor = current month, horizon = 18 months, allowed 1–36. | Built |
| FR-OUT-02 | Several outlooks can exist side by side. Generating one does not supersede the others. | Built |
| FR-OUT-03 | The Outlooks list is paged and searchable. Each outlook can be viewed and exported to Excel. | Built |
| FR-OUT-04 | An outlook can be deleted, **except** when any MPS run was generated from it. In that case deletion is refused and the runs are named. | Built |
| FR-OUT-05 | Numbering is `FCV-<anchor YYYYMM>-<creation MMDDHH>`, with a `-2`, `-3` … suffix within the same hour. | Built |

### 5.6 Consignment stock

| ID | Requirement | Status |
| --- | --- | --- |
| FR-CON-01 | Each week the planner enters the consignment warehouse count for finished goods: product, quantity, unit, count date and lot. | Built |
| FR-CON-02 | The lot number is the **supplier batch** (WMS batch attribute 05), not the WMS internal lot reference. An editable combo box offers that material's supplier batches from WMS; manual entry is also allowed. | Built |
| FR-CON-03 | Expiry is filled from WMS when a batch is found. A manually entered expiry takes precedence. If WMS is down or the batch is unknown, saving still works and the screen says that expiry was left blank. | Built |
| FR-CON-04 | A count is a **complete snapshot** for the warehouse and material. Only the rows from the latest count date count as stock, so a lot left out of the latest count contributes 0. Planners must re-enter every lot that is still on hand. | Built |
| FR-CON-05 | Entering the same warehouse/material/lot/date twice is rejected as a duplicate. | Built |

### 5.7 Available stock and the WMS mirror

| ID | Requirement | Status |
| --- | --- | --- |
| FR-STK-01 | WMS lot data is mirrored read-only into UniOps on a schedule: default every 5 minutes, adjustable in Portal Admin, 0 = manual only. Only one sync runs at a time; manual refresh and the scheduler share the same lock, and a clash returns "already running". Without a WMS connection, refresh answers "not configured". | Built |
| FR-STK-02 | **Available stock definition**, used by both the MPS and purchase suggestions: WMS (quantity − on-hold quantity) over lots whose mapped status is *available* and whose expiry is empty or today or later, **plus** consignment stock from the latest count. Allocated quantity is **not** subtracted, because allocated goods are still on the shelf. | Built |
| FR-STK-03 | WMS quality codes map to available or hold through an editable table: Release → available; Block and Under Inspection → hold. Unknown codes default to hold. Expired is always derived from the expiry date. | Built |
| FR-STK-04 | Sync status records the last attempt and the last *success* separately. Freshness is always shown from the last success. | Built |
| FR-STK-05 | The mirror also holds lot locations, unit, production date and NC QC batch (WMS attribute 04). These serve the Inventory module's WMS screens (lot search, aging, supply per material, open PO lines), which moved out of MRP on 2026-10-14 (see INVENTORY.md). | Built |
| FR-STK-06 | In-transit stock, open purchase orders and NC production orders count as supply in netting. | **Not implemented.** Netting uses on-hand stock only. |
| FR-STK-07 | Minimum remaining shelf life by material, order type or customer (for example stricter for export orders). | Not implemented |
| FR-STK-08 | A "reservable while under inspection" option per material. | Not implemented |

### 5.8 BOM Explorer

| ID | Requirement | Status |
| --- | --- | --- |
| FR-BOM-01 | Multi-level explosion of a product's effective BOM on a chosen date, shown as an expandable tree with per-level and accumulated quantities against an editable basis (default 1,000). | Built |
| FR-BOM-02 | Quantities are shown at usable precision: at least 4 decimals, extended to 6 significant digits so very small quantities do not show as 0. Each line also shows NC's own "batch qty / batch size" figure for side-by-side checking against NC; this figure is display-only. | Built |
| FR-BOM-03 | Where-used lookup for any material (not limited to finished goods). | Built |
| FR-BOM-04 | Problems in the tree (missing BOM, cycle detected, node limit reached) are flagged on the node and listed together in a banner. | Built |
| FR-BOM-05 | Explosion and where-used results export to Excel, with indent level and accumulated quantity. | Built |
| FR-BOM-06 | BOM level labels: powder BOMs show as "Powdering"; names containing "Pasteurized" show as "Pasteurization". | Built |
| FR-BOM-07 | A Sync control refreshes BOMs from NC65 and shows the last sync state. It is visible only with `mdm.bom.write` or `data_maintenance`, and only one sync can run at a time. BOMs cannot be edited in UniOps. | Built |
| FR-BOM-08 | Substitute-material rules (automatic / suggest / forbid), controlled by version, approval and plant. | Not implemented (substitutes are mirrored but not used) |

### 5.9 Capacity rules, calendar and loss rates

| ID | Requirement | Status |
| --- | --- | --- |
| FR-CAP-01 | Plant-wide weekly capacity rules: maximum output (kg), maximum SKU count and minimum output per week, each with an effective-from/to window. Units are fixed to kg. | Built |
| FR-CAP-02 | Per-product minimum lot size (a minimum-output rule scoped to a product), falling back to the plant-wide minimum. | Built |
| FR-CAP-03 | Week exceptions override a rule for one week. A maintenance week has maximum output = 0. Exceptions can be added and deactivated on Capacity Rules, and a maintenance week can be marked or cleared from a week header on the plan matrix. Each week/constraint can have only one exception. | Built |
| FR-CAP-04 | Planning Calendar: week calendar mode and week start day (Section 4.2). | Built |
| FR-CAP-05 | Capacity by product family or production line. | Not implemented (allowed by the data model, not used) |
| FR-CAP-06 | Loss Rates: two plant-wide rates, raw material and packaging, entered as fractions from 0 to 1. Values above 1 are refused as a likely percent-for-fraction mistake. | Built |
| FR-CAP-07 | The Loss Rates screen explains that the packaging rate must stay 0. NC packaging BOMs already include loss: for example 610 cans where 600 are theoretical (+1.67%), and 105 cartons where 100 are theoretical (+5%). | Built |

### 5.10 MPS engine

| ID | Requirement | Status |
| --- | --- | --- |
| FR-MPS-01 | A plan is generated only from a **confirmed** outlook. Demand = each product's monthly forecast netted month by month against available stock (FR-STK-02), with stock carried forward. Only months with a positive net requirement are planned. | Built |
| FR-MPS-02 | Demand stays monthly. The engine places production in **weeks**. Each demand month is shifted earlier by the production lead time (in weeks) and spread across that month's weeks. | Built |
| FR-MPS-03 | Placement follows three principles: (P1) keep a product's production consecutive, because changeovers are expensive; (P2) produce every week when the month is not full; (P3) aim for one product per week. When capacity is tight, the engine uses largest-first packing, and a product that fits in one week is never split. When capacity is spare, it spreads production down to the weekly minimum output and leaves the remaining weeks empty. | Built |
| FR-MPS-04 | Volume that does not fit overflows backwards into earlier weeks (pre-build), across month boundaries. Every week earlier than its target must pass the shelf-life rule. Volume that cannot be placed becomes an explicit **capacity gap** line; nothing is silently dropped. | Built |
| FR-MPS-05 | Minimum lot size: a product's output in any week is either 0 or at least its lot size. A smaller requirement is rounded up to a full lot (the extra is recorded as surplus and offsets later months), and the full lot is placed whole in one week. If no week fits, the engine looks in the same month, then earlier, then later (marked *late production*). If weekly capacity physically cannot reach the lot, capacity wins: the lot is split and marked *below minimum lot*. Surplus that would outlive its shelf life is produced anyway and flagged. | Built |
| FR-MPS-06 | Months fully covered by surplus carried in from earlier show a zero-quantity "covered" line, so the cell reads "already made" rather than "no demand". | Built |
| FR-MPS-07 | A maintenance week (maximum output 0, or maximum SKUs < 1) is skipped, and its volume is placed in the month's other weeks. | Built |
| FR-MPS-08 | Demand that the lead time would place before the current week is clamped to the current week and flagged *lead shortfall*. | Built |
| FR-MPS-09 | Frozen zone: the first N months (default 3) are copied unchanged from the plan in force as locked lines. The engine cannot place anything in them, and their lines cannot be edited. Uncovered demand for a frozen month goes to the first free week, flagged lead shortfall. The first plan ever generated (nothing in force yet) has no frozen zone. The run records the number of frozen months actually applied. | Built (no settings screen; see Open Items) |
| FR-MPS-10 | Products with no shelf life on record are never pre-built, and the run summary names them. | Built |
| FR-MPS-11 | Each run snapshots the week mode, week start, lead time and frozen months. Viewing, recalculating and adjusting always use the run's own values, not current settings. Capacity rules and minimum lots are **not** snapshotted: a recalculation uses today's rules. | Built |
| FR-MPS-12 | Opening stock and forecast context are stored on each plan line when the run is generated, so a released plan does not drift as stock changes. | Built |
| FR-MPS-13 | Known limitation: one reduced (not closed) week in a month makes the whole month's packing use the tight-capacity method, which suppresses spreading. Accepted. | Accepted limitation |

### 5.11 Production Plan page and plan versions

| ID | Requirement | Status |
| --- | --- | --- |
| FR-PLN-01 | Matrix view: each product has three rows (Demand / Available / Planned), and the columns are plan weeks grouped into collapsible months. Week numbers count through the year. Weekly totals are pinned at the bottom. Available = opening stock + surplus carried in. | Built |
| FR-PLN-02 | Production is shown in the week it is made, with the demand month it serves in a tooltip. Cell markings: capacity gap (red, takes priority), lead shortfall (amber), below minimum lot, late production, surplus expiry risk, "No BOM" badge. Frozen-zone columns are greyed and cannot be clicked. | Built |
| FR-PLN-03 | Clicking a Planned cell opens a drawer to change quantity, move to another week (past weeks are not offered; shelf life is re-checked) or lock the line. Capacity-gap lines cannot be locked. Recalculate re-plans everything except locked lines. | Built |
| FR-PLN-04 | A "planning rules" panel beside the matrix describes the rules this run was built with: week definition and start day, lead time, frozen months, minimum lot behaviour. | Built |
| FR-PLN-05 | Plan versions: runs are grouped by horizon start month. Each run is draft, released or superseded. **Exactly one run in the whole system is the plan in force**, and the database enforces this. | Built |
| FR-PLN-06 | Release makes a run the plan in force. It replaces all MRP demand with this run's weekly production, **including minimum-lot surplus** and excluding capacity-gap lines. Releasing a newer group supersedes every run of older groups. Releasing an older group's draft is refused. Re-releasing the plan in force does nothing and reports success. A released run cannot be changed. | Built |
| FR-PLN-07 | Within the current group, an approver can switch the plan in force to another released version. The stored lines are replayed, not recalculated. Switching to an older group is refused. | Built |
| FR-PLN-08 | The URL carries the selected version, so a page refresh returns to the same plan. Without a selection, the page opens the plan in force, otherwise the newest run. Superseded runs stay readable. | Built |
| FR-PLN-09 | Compare: a run is compared with the plan in force, or with the version it replaced when it *is* the plan in force. Changes are shown cell by cell (increases ▲ green, decreases ▼ red, removed cells struck through), with a summary and changed months marked on the month header. A first version has nothing to compare against and shows no differences. | Built |
| FR-PLN-10 | When the outlook being released is not the newest one, the page warns but does not block. | Built |
| FR-PLN-11 | Export the matrix to Excel, in kg or tonnes, grouped by month. | Built |
| FR-PLN-12 | Numbering is `MPS-<horizon YYYYMM>-<creation MMDDHH>`, with a suffix within the same hour. | Built |

### 5.12 Supply parameters

| ID | Requirement | Status |
| --- | --- | --- |
| FR-SUP-01 | List, search, add, edit and remove supply parameter rows (material, supplier, lead time in days, MOQ, order multiple, primary). Material and supplier names are shown, and the unit is stated. Only purchased materials (`CR*`, `CP*`) are offered. | Built |
| FR-SUP-02 | Bulk paste from Excel is the main way to enter rows. Rows are matched on material + supplier and inserted or updated. Errors are reported per row; the batch is not rolled back as a whole. | Built |
| FR-SUP-03 | Each material can have at most one primary supplier. A second primary gets a readable row error. | Built |
| FR-SUP-04 | A row can be moved to another supplier. | Built |
| FR-SUP-05 | The screen counts materials that have suppliers but no primary. | Built |
| FR-SUP-06 | Write permission must be the same on screen and on the server. | **Defect.** The screen enables editing for `mrp.param.write`, but the server accepts only `data_maintenance` or `mdm.bom.write` (see Open Items). |
| FR-SUP-07 | Supplier delivery calendars; switching supplier on an individual suggestion. | Not implemented |

### 5.13 Purchase suggestions (Phase 1C)

| ID | Requirement | Status |
| --- | --- | --- |
| FR-PUR-01 | A purchase run is calculated from the **plan in force**. If there is none, the request is refused ("release a plan first") rather than returning an empty list. | Built |
| FR-PUR-02 | Explosion uses each product's effective BOM. Quantities are already per unit and are never divided again. Only leaves become requirements: a component with its own BOM is made, and passes its quantity down. A product with no BOM is reported, not silently skipped. Products whose BOM request *failed* are counted separately from products with *no BOM*, so a service outage cannot look like "nothing needed". | Built |
| FR-PUR-03 | Loss is added once, at the rate for the component's own category (packaging `CP*` → packaging rate; everything else → raw rate). Children are exploded from the parent's base quantity, so losses never compound down the levels. | Built |
| FR-PUR-04 | Stock netting: each component's available stock (FR-STK-02) is used up week by week, earliest week first, and carried forward. It does not reset each week. | Built |
| FR-PUR-05 | Order date = need week − supplier lead time. The quantity is raised to the MOQ first, then rounded up to the order multiple (this order keeps the result at or above the MOQ). Weeks with no net requirement produce no line. The primary supplier is used; a material with no primary takes its first supplier. | Built |
| FR-PUR-06 | Unknowns are flagged, never assumed. Each line carries separate flags for supplier missing, lead time missing (calculated as 0 days) and order date already passed (with how many days ago). The run summary counts each flag. | Built |
| FR-PUR-07 | Each run is stored and replayed exactly as calculated. Loss rates are snapshotted on the run. Numbering starts with `PUR-`. | Built |
| FR-PUR-08 | The screen opens with lines sorted by order date and can be re-sorted by any column header (EPMS.md UI-SORT-001), shows the flag counts at the top, has a legend, explains MOQ increases in a tooltip, and can hide completed lines. When both loss rates were 0, it notes that in-transit stock is not counted yet. | Built |
| FR-PUR-09 | Each line has a status: pending / ordered / ignored. This is in place for a future requisition link. | Built |
| FR-PUR-10 | Excel export sorted by order date. Flags are written as text columns, not colours. | Built |
| FR-PUR-11 | Convert suggestions into EPMS purchase requisitions and write back the requisition number and status (see EPMS.md). | Not implemented |
| FR-PUR-12 | Net against in-transit stock and open NC purchase orders. | Not implemented |
| FR-PUR-13 | Flag delivery risk when the requested arrival is earlier than the supplier's earliest possible delivery. | Partly built; only "order date passed" is flagged. |
| FR-PUR-14 | Before calculating, the system should check that supplier data actually loaded. | **Defect.** If the supplier service cannot be reached, every line is marked supplier/lead time missing instead of the run failing (see Open Items). |

---

## 6. Milk-powder industry rules: status

| Rule | V1.0 requirement | Status |
| --- | --- | --- |
| Shelf life | Batches below a minimum remaining shelf life are not available for that demand; configurable by material, order type and customer level | **Partly built.** Expired stock is excluded. Pre-build is limited by shelf life × (1 − safety margin), counted in calendar days. Surplus expiry risk is flagged. No minimum-remaining thresholds per order or customer. |
| Stock status limits | Under inspection, blocked and rejected stock unavailable by default; status changes trigger recalculation | **Partly built.** Hold statuses are excluded through the mapping table; unknown codes count as hold. No automatic recalculation. |
| Recipe loss / yield | Suggested input inflated by standard yield and loss; show theoretical, loss and total quantities | **Partly built.** Two plant-wide loss rates are applied once per component. Lines show gross, available, net and suggested quantities; loss is not shown separately. Yield is not applied: in this NC data, yield repeats the batch-size divisor and would shrink quantities twice. |
| Packaging conversion | Finished-good demand drives both powder and packaging | **Built**, through multi-level explosion. |
| Substitutes | Controlled by version, approval and plant; auto / suggest / forbid | Not implemented |
| Rework material | Not a stable supply source by default | Not implemented (rework items are excluded from finished goods) |
| Changeover / line clearance | Risk notice only | **Built in effect.** Minimum lot size and the consecutive-production rule avoid token runs. There is no separate changeover-loss notice. |
| WMS read-only integration | No write-back of reservations or suggestions | **Built.** |
| Traceability | Suggestions trace back to demand source and master-data version | **Partly built.** A purchase run links to its source plan, a plan links to its outlook, and outlooks and plans are immutable. The BOM version used is not stored on suggestion lines. |
| Planning unit | — (added) | All finished-goods planning is in kg, and mixed units are refused. |

---

## 7. Pages

| V1.0 page | As built | Status |
| --- | --- | --- |
| Planning cockpit | — | Not implemented |
| MRP run console | Production Plan (Generate / Recalculate / Release / version picker); Purchase Suggestions (Calculate) | Partly built; no run log or stop/rerun |
| Planner workbench | — | Not implemented |
| Material supply/demand detail | Production Plan matrix (finished goods); BOM Explorer; Inventory module (stock per material) | Partly built |
| Purchase suggestions | Purchase Suggestions | Built (no requisition conversion) |
| Internal supply reference | Production Plan | Built for finished goods |
| Exception message centre | — | Not implemented |
| Parameter maintenance | Capacity Rules (rules, week exceptions, planning calendar, loss rates); Supply Parameters | Built; no approval or rollback |
| Report centre | Excel exports only | Not implemented |
| Interface monitoring | Portal → Admin → WMS Sync; BOM Explorer sync panel | Partly built |
| — (added) | Sales Forecast (grid, outlooks, intent products) | Built |
| — (added) | Consignment Stock | Built |

Interaction principles kept from V1.0: drill from summary to detail, batch-friendly entry (paste), and every flag explained on screen.

---

## 8. Data model and interfaces (as built)

### 8.1 Main data objects

| Object | Key fields |
| --- | --- |
| Demand series | material, month, quantity (kg); change log with old/new value, editor and time |
| Forecast version (outlook) and lines | version number, anchor month, horizon, status, intent flags |
| Intent product | placeholder code, name, bound-to code, status |
| Consignment stock | warehouse, material, supplier batch, count date, quantity, unit, expiry, expiry source |
| WMS lot mirror | material, WMS lot, supplier batch, QC batch, WMS status, mapped status, quantity, on-hold, allocated, expiry, production date, unit; lot locations; sync state (last attempt, last success, rows, error) |
| Capacity rule / exception | scope, constraint type, limit, effective window / week, active |
| Planning parameter | key, value, updated by, updated at |
| MPS run | run number, outlook, horizon, status, plan-in-force flag, released at, lead weeks, week mode, week start, frozen months, safety margin, summary stats |
| MPS line | material, demand month, plan week, quantity, weeks early, flags (pre-build, gap, lead shortfall, below minimum lot, late, covered, surplus expiry risk), surplus and carry-in quantities, locked / manually adjusted, demand and opening-stock snapshot |
| MRP demand | material, plan week, demand month, quantity, type `mps` |
| Purchase run / line | run number, source plan, loss rates, stats; material, need week, order date, gross/available/net/suggested quantities, MOQ increase, supplier, lead time, three flags, status |

### 8.2 Interfaces

| Interface | Frequency | Failure handling |
| --- | --- | --- |
| WMS → mirror | Every 5 minutes by default; manual refresh | One sync at a time. On failure the previous snapshot stays and the last-success time does not move. No write-back. |
| NC65 BOM → MDM | Manual (BOM Explorer Sync) | One sync at a time; status shown. |
| ERP materials → MDM | MDM's own schedule | — |
| MDM BOM explosion / supply parameters / shelf life / names → MRP | Each request | Name lookups degrade to blank. A failed BOM explosion is counted on the purchase run. Missing shelf life means no pre-build, and the run says so. Supply-parameter failures are not reported (FR-PUR-14). |
| Customer orders, MES, purchase-execution feedback | — | Not implemented |

---

## 9. Non-functional requirements

| ID | Requirement | Status |
| --- | --- | --- |
| NFR-01 | Performance: a full WMS snapshot takes about 5 seconds. Plan and purchase runs complete interactively. The V1.0 targets (15-minute incremental / 60-minute full run) were never formally load-tested. | Partly verified |
| NFR-02 | Each planning engine is a pure calculation with no database, clock or service calls, covered by automated tests. | Built |
| NFR-03 | Quantities use exact decimals end to end, never floating point. | Built |
| NFR-04 | Security: single sign-on through UniOps identity; a permission key on every endpoint; BOM reads restricted. | Built (see FR-SUP-06) |
| NFR-05 | Integration: WMS read-only; MRP holds no stock ledger. | Built |
| NFR-06 | Positioning: guidance tool, with no MES-level execution. | Built |
| NFR-07 | Extensibility: the data model already allows multi-plant, product-family and line capacity, and requisition linking. | Partly; groundwork only |
| NFR-08 | Observability: alerts for interface failure, run timeouts and data anomalies. | Not implemented (freshness display only) |
| NFR-09 | UI language is English. Dates are shown without time-zone shift. Touch targets are at least 44 px. | Built |

---

## 10. Delivery phases (as delivered)

| Phase | Content | Status |
| --- | --- | --- |
| 0: Foundations | Materials, units, NC BOM mirror and canonical BOMs, supply-parameter table, WMS mirror with status mapping, permission keys, MRP service | Built |
| 1A: Forecast, stock, BOM | Forecast grid, consignment stock, net requirement, BOM explosion and where-used, BOM Explorer | Built (forecast later redesigned as a continuous series with outlooks) |
| 1B: MPS | Capacity rules, MPS engine and Production Plan matrix, release to MRP demand, production lead time | Built; moved from monthly to weekly planning |
| 1B+: Plan refinements | Intent products, weekly MPS and calendar, minimum lot, week start day, frozen zone, plan versioning, plan comparison | Built |
| 1C: Material planning | Loss rates, supply parameters, purchase suggestions | Built |
| Operations | Scheduled WMS sync, freshness display; WMS Inventory moved to the Inventory module | Built |
| Next | Requisition conversion, in-transit netting, exception centre, workbench, reports, safety stock | Not started |

---

## 11. Acceptance scenarios

| Scenario | Precondition | Expected result | Status |
| --- | --- | --- | --- |
| Forecast vs order overlap | Forecast and order for the same product in the same week | Consumed or added per policy | Not applicable (no order input) |
| Insufficient shelf life | Batch expired | Excluded from available stock | Built (expiry only; no minimum-remaining threshold) |
| WMS release triggers recalculation | A hold batch becomes available in WMS | Automatic recalculation | Partly built: the mirror updates within the sync interval; the plan is not recalculated |
| Packaging shortage | Powder sufficient, cans short | Packaging purchase suggestion | Built (no "blocked" mark on the production plan) |
| Recipe change | New BOM version takes effect | New version used for explosion | Built (highest approved version effective on the date) |
| Minimum lot | Net requirement below the lot size | Raised to the lot size, extra shown | Built (MPS minimum lot; purchase MOQ) |
| Supplier lead time exceeded | Order date already passed | Flagged | Built ("order date passed") |
| Exception closure and audit | Planner closes a shortage exception | Closer, time and note recorded | Not implemented |
| Frozen zone | Plan in force exists; regenerate | First N months identical to the plan in force | Built |
| Single plan in force | Release a new plan | Previous plan superseded; demand replaced, not added | Built |
| Packaging loss not doubled | Packaging rate = 0 | Packaging quantity equals the BOM | Built |
| BOM quantity per unit | Explode a known product (700 g × 6 can) | About 1.45 cans/kg, not ×1000 | Built |

---

## 12. Risks and dependencies

- **Master-data quality** is still the biggest risk: BOM versions (several products have more than one default BOM), packaging BOMs with built-in loss, shelf life missing for finished goods, and supply parameters that start out empty.
- **Stock freshness**: plans use the mirror as of generation time. A stopped scheduler (interval 0) or a WMS outage leaves stock data old. The freshness display shows this, but nothing alerts on it.
- **No forward demand source**: plan quality depends entirely on the hand-maintained forecast.
- **Netting covers on-hand stock only**: without in-transit and open-PO netting, suggestions are *requirements*, not a net position. Buyers must check what is already on order.
- **Units**: units are checked for finished goods. For purchased components, WMS stock and BOM quantities are assumed to use the same unit; this is not verified.
- **Boundary with WMS**: MRP's internal allocation is not reflected in WMS, and WMS allocations are not subtracted in MRP.
- **Scope creep**: APS, MES and supplier collaboration stay out of scope.

Pre-launch conditions from V1.0 still apply: settle the definitions for materials, BOMs, packaging conversion, shelf life and quality status; agree responsibilities between planning, purchasing, warehouse, quality and production; replay at least one full month of history in user acceptance testing.

---

## 13. Open Items

| ID | Item | Status |
| --- | --- | --- |
| OI-01 | Frozen-months setting (default 3) has no screen; it can only be changed through the API | Open |
| OI-02 | Supply Parameters write permission differs between screen (`mrp.param.write`) and server (`data_maintenance` / `mdm.bom.write`). Users with only one of these see edit controls that always fail, or no edit controls at all | Defect |
| OI-03 | Sales Forecast enables editing for `data_maintenance`, but the server accepts only `mrp.demand.write` | Defect |
| OI-04 | Purchase run does not fail when supplier parameters cannot be loaded; every line shows supplier/lead time missing instead | Defect |
| OI-05 | Purchase netting ignores in-transit stock, open NC purchase orders and NC production orders | Not implemented |
| OI-06 | Convert suggestions into EPMS purchase requisitions and write back their status | Not implemented |
| OI-07 | Exception message centre (typed exceptions with severity, owner, close/ignore/annotate/reopen, subscriptions); the `mrp.exception.handle` key is unused | Not implemented |
| OI-08 | Planner workbench (single screen for suggestions, exceptions and stock risk, with batch actions) | Not implemented |
| OI-09 | Simulation / what-if runs and parameter trial calculation | Not implemented |
| OI-10 | Report centre (supply/demand balance, shortage list, stock health, execution rate, planning accuracy) | Not implemented |
| OI-11 | Safety stock, reorder point, fixed/max lot and period-grouping lot strategies | Not implemented |
| OI-12 | Holiday / receiving / production calendars; QC buffer time in back-scheduling | Not implemented |
| OI-13 | Minimum remaining shelf life per material / order type / customer; "reservable while under inspection" option | Not implemented |
| OI-14 | Event-triggered or scheduled MRP recalculation (on WMS status change, urgent order) | Not implemented |
| OI-15 | Substitute-material and rework-material rules | Not implemented |
| OI-16 | Customer-order and promotion demand import; forecast consumption policy | Not implemented (no source system) |
| OI-17 | Parameter change approval / dual review / rollback; general audit log for parameters and line overrides | Not implemented |
| OI-18 | Interface monitoring with alerts and retry queue | Not implemented |
| OI-19 | Planner-group / plant scoping of data access | Not implemented |
| OI-20 | Capacity by product family or production line | Not implemented |
| OI-21 | Supplier delivery calendars; per-suggestion supplier switch; delivery-risk flag beyond "order date passed" | Not implemented |
| OI-22 | `mrp.proposal.export` key exists but is unused (exports are gated by `mrp.report.view`) | Open |
| OI-23 | Unit consistency between WMS stock and BOM quantities is not verified for purchased components | Open |
| OI-24 | One reduced (not closed) week makes the whole month use tight-capacity packing (FR-MPS-13) | Open (accepted limitation) |
| OI-25 | BOM version used is not stored on purchase suggestion lines (traceability) | Not implemented |
| OI-26 | Confirm ERP shelf life is on record for every finished good; products without it are never pre-built | Open |
