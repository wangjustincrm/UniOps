# Inventory — Product Requirements Document

**Version:** 1.0
**Date:** 2026-10-16
**Status:** Live in production (first released 2026-10-15). Phase 1 complete; the items in §6 remain.
**Scope:** A standalone UniOps module that shows warehouse (WMS) stock and the three NC inventory ledgers side by side, on any date, and explains the differences between them.

**What's in this version**

- First edition, written from the system as it runs on main.
- Five pages: WMS Inventory, NC Inventory (Stock / Cost / Ledger), Reconciliation, Trace, Settings.
- Two read-only mirrors owned by the module (NC inventory ledgers; WMS transaction history), each checked against its source after every sync.
- Three permission keys that keep quantities and money apart.

---

## 1. Overview & purpose

### 1.1 Business problem

The plant's stock is recorded in four places, and they rarely agree:

| Source | Owner | Question it answers |
|---|---|---|
| **WMS** (Flux) | Warehouse | What is physically on the shelves, by lot and location |
| **NC stock (IC)** — supply-chain inventory | Supply chain | What NC thinks is on hand, by warehouse, material and batch |
| **NC cost (IA)** — inventory accounting | Cost accounting | Quantity and value per material, by accounting period |
| **NC general ledger (GL)** — inventory accounts | Finance | Balance of the inventory accounts, by material |

Before this module, comparing them meant pulling four separate reports, matching material codes and batch numbers by hand, and guessing why the figures differed. Batch numbers do not line up directly: WMS's internal lot number never matches NC's batch code, so the comparison has to go through the QC batch and supplier batch numbers. Several common differences are expected and harmless, for example stock in a warehouse that NC does not cost, or goods shipped but not yet invoiced. People had to work those out again for every review.

On 2026-10-14 the business decided to build Inventory as **its own module**, not as part of MRP or Finance, because more inventory features are planned. Phase 1 delivers:

1. **WMS Inventory.** The existing MRP inventory screen moved into this module and renamed.
2. **NC Inventory.** NC's three ledgers (IC, IA, GL) as of any date.
3. **Reconciliation.** A four-way comparison with automatic attribution of differences to named causes, and an optional batch-level breakdown.
4. **Trace.** NC and WMS movements for one material or batch side by side, with the point where the two first diverged.

The module only reads from NC and WMS. It never writes back to either, creates no Tasks and sends no notifications. This follows the Finance positioning, where NC is the system of record and UniOps is an assistive layer (see FINANCE.md).

### 1.2 Users, roles & permissions

| Audience | Typical use |
|---|---|
| Warehouse / planning | WMS Inventory: stock by supplier batch, shelf life, on-order |
| Supply chain / inventory control | NC stock, Reconciliation (IC−WMS), Trace |
| Cost accounting / finance | NC cost and ledger, Reconciliation (IA−IC, GL−IA) with amounts |
| System administrators | Settings: sync, warehouse classes, parameters |

Access is controlled by three keys in Portal → Access Control (see PORTAL.md):

| Key | Grants | Enforcement |
|---|---|---|
| `inventory.view` | Every page and every **quantity**: WMS, IC, IA quantity, GL quantity, the comparison, Trace, read-only Settings | Required by every inventory-api endpoint. Without it the module shows a no-access screen. The Portal card and sidebar entry are hidden. |
| `inventory.cost.view` | Every **amount**: IA amount and unit price, GL amount, GL−IA amount, the amount part of a cause, GL by source, the impairment (1471) amount, and tie-out findings measured in money | The server strips amounts. Without this key, amount fields come back empty and the page hides those columns. The browser is never trusted to hide them. |
| `inventory.admin` | Settings changes and the manual Refresh of the NC and WMS-history syncs | Required by every write endpoint. Write controls are shown only to holders. |

- At go-live **only `system_admin`** holds the three keys. The administrator assigns them to other roles in Access Control; no other defaults are seeded.
- `system_admin` always passes, both on the server and in the module's own permission check.
- The WMS Inventory page reads from the MRP service (see §2.1). Those reads accept `mrp.report.view` **or** `inventory.view`. The WMS stock Refresh button on that page accepts `mrp.param.write` **or** `inventory.admin`, and is shown to exactly those users.

### 1.3 Entry points

- Portal home card **Inventory** and the Portal sidebar entry (Operations group), both gated on `inventory.view`.
- In MRP, the sidebar entry **Inventory ↗** opens this module's WMS Inventory page in a new browser tab. The old MRP inventory page has been removed (see MRP.md).
- Reconciliation rows (material or batch group) link to Trace with the material, batch and date filled in.
- The module opens on **NC Inventory**, which is pinned as the first tab. Trace opens **one tab per material**.
- The module can be embedded in another module as an iframe. In that mode it shows the page only, with no sidebar or tabs.

---

## 2. Data sources & definitions

### 2.1 Where each figure comes from

| Figure | Source | Copy held in UniOps | Refreshed by |
|---|---|---|---|
| WMS current stock (lots, locations, quality status, expiry) | Flux WMS | MRP's WMS stock mirror (shared with MRP purchase suggestions) | MRP service, on the WMS sync interval set in Portal → Admin |
| WMS stock on a past date, WMS movements | Flux WMS transaction log | Inventory's WMS-history mirror | Inventory service, on the Inventory sync interval |
| NC stock (IC) | NC stock movements and current on-hand | Inventory's NC mirror | Inventory service, on the Inventory sync interval |
| NC cost (IA) | NC inventory accounting detail ledger, month-end balances, period close | Inventory's NC mirror | Same |
| NC GL | NC voucher lines on the inventory accounts | Inventory's NC mirror | Same |

The module has its own direct read-only connections to NC and to Flux. It does **not** reuse Finance's IA copy (see AI-FINANCIAL-ANALYSIS.md), so the two modules can be changed independently. Current WMS stock is still synced by MRP. Moving that sync into Inventory is a later step (§6).

### 2.2 Company slice

Each NC module identifies "Canada" with a different key:

- **IC** filters on the stock organisation.
- **IA** filters on the cost region and accounting book.
- **GL** filters on the accounting book.

The three keys correspond one to one. WMS is filtered to the Canada warehouse and customer. The WMS customer code is **not** an NC organisation code.

### 2.3 WMS (Flux)

- **Current stock.** Lot-level quantity, allocated, on hold, quality status (Release / Block / Under Inspection), production, inbound and expiry dates, supplier batch, and NC QC batch.
- **History.** Rebuilt from the WMS transaction log. Only completed transactions count, and quantities are taken in base units. Each transaction type moves stock as follows:

  | Type | Effect |
  |---|---|
  | Receipt (IN) | Adds to the target lot |
  | Shipment (SO) | Removes from the source lot |
  | Adjustment (AD) | Removes the "from" side and adds the "to" side |
  | Transfer (TR) between lots or SKUs | Moves the quantity between lots |
  | Location-only transfer, put-away, pick, move, hold, release | No change in total |

- **History start.** WMS went live in January 2025, but its stock history starts with the opening-stock import on **2025-02-03**. This date is fixed by business decision and is not derived from the data. Before that date WMS has no figures. Every page that shows WMS data displays this note, and shows it as a warning when the chosen date is earlier.
- **Units.** WMS's base unit is unreliable: most SKUs default to KG, including packaging counted in pieces. Units are shown for information only and never used to explain a difference.

### 2.4 NC stock (IC)

- **Today:** NC's current on-hand table, by warehouse × material × batch × stock state. It also gives the **locked** and **reserved** quantities, and **available** = on-hand − locked − reserved, floored at 0. Rows with zero on-hand but a leftover lock are counted and reported, not listed.
- **Past date:** the sum of all NC stock movements up to the cutoff (§2.7). NC does not keep stock state or locks historically, so a past date shows quantities only, and the page says so.
- **Unsigned documents.** Unsigned stock documents already move IC quantity. They are kept, because their effect on IA is one of the explained causes.
- **Warehouse classes.** Each NC warehouse is assigned a class in Settings. Defaults are seeded at install:

  | Class | Default warehouses | Compared with WMS? | Shown |
  |---|---|---|---|
  | WMS-managed | SC11, SC12, SC13, SC16 | Yes, the only class compared with WMS | Yes |
  | External / consignment | SC05, SC06, SC07, SC08, SC15, SC17 | No | Yes, separately |
  | Production line-side | Pro01, Pro02 | No | Yes, separately |
  | Transient | Pro03, 015, 016, 021 | No | Not listed separately, but **counted in the NC total** (IA has no warehouse dimension, so leaving them out would create a false IA−IC difference) |
  | Other | — | No | Counted in the NC total |
  | *Unclassified* (no row) | Any warehouse NC added since | No | Flagged in Settings. Counted in the NC total. |

- **SC16** is both WMS-managed and **not costed** in NC. Its stock is in IC and WMS but never in IA. This is a named cause, not an exclusion.

### 2.5 NC cost (IA)

- **Grain.** Cost region × book × material × accounting period. **IA has no warehouse or batch dimension for value**: cost exists only at material level. The detail ledger carries batch and warehouse, so quantities can be broken down by them. Amounts cannot.
- **By accounting period** (business decision): the balance as of D includes all periods before D's month, plus rows of D's month dated on or before D. When D is a month end, the whole period is included, matching NC's own month-end balance even for back-dated entries.
- **Period status** is shown for D's month:
  - **Closed.**
  - **Open, costed.**
  - **Open, not yet costed.** The number of outbound rows without a cost is shown, and IA amounts are flagged as incomplete.

### 2.6 NC general ledger (GL)

- **Inventory accounts:**

  | Account | Content |
  |---|---|
  | 1402 | In transit |
  | 1403 | Raw materials |
  | 1404 | Finished goods |
  | 1405 | Semi-finished goods |
  | 1406 | Packaging |
  | 1407 | Low-value consumables |
  | 1408 | Goods held for others |
  | 1471 | Impairment provision |

  Accounts 1402–1406 carry a material auxiliary with quantities, so they can be compared per material.
- **1471 impairment** is mirrored but always shown as its own line and **never compared per material** (business decision). Lines without a material auxiliary are also kept as their own "(no material)" line, never dropped.
- **Balance as of D:** D's year only (balances are never added across years). This includes the opening period "00", all earlier periods, and D's period up to vouchers prepared on or before D (the whole period on a month end).
- **Unposted vouchers** are **included by default**, with a switch to exclude them.

### 2.7 Time zones and "as of D"

- D is a calendar date in **Toronto**.
- NC writes business times on its application server's clock, which is set to **China time**. This was inferred from the data and confirmed by business decision. As a result, "end of D in Toronto" becomes a cutoff about 12–13 hours later on NC's clock. The cutoff and the zone used are shown on the NC page.
- The NC time zone can be switched to Toronto in Settings, for the day NC is fixed or to test a suspicion.
- **Date-only NC rows.** Some rows have a time of exactly 00:00:00: about 12,000 IC rows and 12,000 IA rows, created with a date and no time. These are compared **as dates**, never converted. Converting them would move them to the previous Toronto day.
- **WMS times** are already in Toronto time and are used as is.

### 2.8 Batch matching between WMS and NC

- WMS's internal lot number **never** matches NC's batch code (0 of about 3,400 at analysis).
- Matching uses two numbers on each side:

  | Side | QC batch | Supplier batch |
  |---|---|---|
  | WMS | Lot attribute 04 | Lot attribute 05 |
  | NC | Batch code | Vendor batch code |

- **Rule (business decision):** after removing all whitespace and upper-casing, a WMS lot and an NC batch of the same material are the same batch if **either** number matches either number. Linking is transitive, so one WMS lot that matches two NC batches, or two lots that match one batch, form **one batch group**. Nothing is counted twice.
- **Coverage at analysis:** about 69% of active WMS batch combinations matched (95% by quantity). Almost all of the rest were by-products NC had stopped recording (below).
- **Materials not tracked by batch in NC.** NC stopped recording **CF00AF** (Animal Feed) and **CF00WT** (Waste Powder) from 2026-01-01. They are compared at material level only, and any WMS−IC difference after that date is the explained cause "NC stopped recording". The list is maintained in Settings.

### 2.9 Known source-data limitations

| Limitation | Effect in the module |
|---|---|
| WMS history starts 2025-02-03 | No WMS figures before that date. Reconciliation shows WMS as "not live". Trace windows start there. |
| NC stock state and locks exist only for today | Past-date NC stock shows quantities only |
| WMS's own opening stock was entered as manual "QC" receipts with no NC counterpart | Trace labels them "Manual opening import (WMS)" and does not pair them |
| Two WMS lots do not tie out inside WMS itself: a receipt line edited after posting (CR0241), and the virtual SKU FULLCARTON, which holds stock with no transactions | They appear in every WMS tie-out as findings. By design, there is no allow-list. |
| WMS base units default to KG | The unit check is shown for information only |

---

## 3. Functional requirements

### 3.1 Module shell

| ID | Requirement |
|---|---|
| INV-001 | The module shall present five pages in its sidebar, in this order: WMS Inventory, NC Inventory, Reconciliation, Trace, Settings. |
| INV-002 | Without `inventory.view` the module shall show a no-access screen with a link back to the Portal. If the permission check itself fails, it shall show a retryable error, not a "no access" message. |
| INV-003 | NC Inventory shall be the pinned landing tab. Trace shall open one tab per material, so links from other pages open the right material and do not reuse a stale tab. |
| INV-004 | Every page's date, filters and active tab shall live in the URL, so a view can be linked to and the tab restores correctly. Dates shall be plain Toronto calendar dates and never parsed through a time-zone conversion. |
| INV-005 | Decimal quantities and amounts shall travel as text and be formatted for display without floating-point rounding. Totals across different units of measure shall be labelled "summed across units of measure". |
| INV-006 | Every WMS-facing page shall show the WMS history note (§2.3). Where the chosen date is before 2025-02-03, the note shall appear as a warning. |
| INV-007 | Long tables shall scroll inside their own container with the header row fixed. |

### 3.2 WMS Inventory

Current WMS stock from MRP's WMS mirror. The page was moved here from MRP; its behaviour is unchanged. One page, three tabs: Batches, Aging, Materials.

| ID | Requirement |
|---|---|
| INV-010 | The page shall show the **age of the WMS snapshot**, measured from the last *successful* sync and refreshed every minute. The indicator shall turn amber when the snapshot is older than twice the sync interval (at least 15 minutes), and red when the last sync failed. It shall be hidden when WMS is not configured. |
| INV-011 | Users with `inventory.admin` or `mrp.param.write` shall see a **Refresh** button that runs a WMS stock sync immediately. |
| INV-012 | A **Material class** filter shall apply to all three tabs. The default is **0102 Raw Ingredient**; the other options are packaging, standardised milk, silo powder, finished products, chemical, mechanical, laboratory, or all. |
| INV-013 | **By-products** CF00AF and CF00WT shall be excluded by default. A visible switch shall add them back whenever the finished-goods class or "all classes" is selected. |
| INV-014 | **Raw milk** (class 0101) shall never be counted as stock or as on order, and the page shall say so. |
| INV-015 | **Batches tab.** Stock grouped by **supplier batch**: one row per material × supplier batch × warehouse. Columns: material, supplier batch, quantity, NC QC batch numbers, number of WMS lots, shelf life, production date, expiry date. The internal WMS lot number shall not be displayed, but shall be searchable. |
| INV-016 | The Batches tab shall support search (material, name, supplier batch, NC QC batch), a status filter (any / available / on hold / expired), sorting by material, supplier batch, quantity, production date or expiry, and server-side paging. |
| INV-017 | Where a batch's lots disagree on expiry or production date, the batch row shall show the earliest date and flag that the lots differ. Where they disagree on quality status, the row shall show "Mixed". |
| INV-018 | Expanding a batch shall list where it physically sits, one row per location × handling unit: location, zone, tracking ID, quantity, dates, quality status. |
| INV-019 | A summary block shall show, **one line per unit of measure**, the total, available, expired, blocked and expiring-soon (90 days) quantities. Different units shall never be added together. The figures overlap and are not a split of the total. |
| INV-020 | **Aging tab.** Batches in shelf-life bands: expired, under 30 days, 30–60, 60–90, 90–180, over 180. Each band is clickable and lists its batches with expiry and days remaining. |
| INV-021 | **Materials tab.** One row per material: on hand, available, on hold, expired, next expiry, on order, earliest arrival. Expanding a row shall list the open purchase-order lines behind "on order" (PO, supplier, ordered, received, still owed, expected date). A switch shall hide materials with no stock and nothing on order. |
| INV-022 | **Available** shall follow the MRP definition: quantity minus on-hold, over lots that are Release and not expired. The allocated quantity is **not** subtracted. |
| INV-023 | The page shall point users to Reconciliation for WMS stock on a past date and for the comparison with NC. |

### 3.3 NC Inventory

The three NC ledgers as of any date up to today. Three tabs: **Stock (IC)**, **Cost (IA)**, **Ledger (GL)**. A shared header carries the date picker (default today, future dates not allowed), the material-class filter and the search box.

| ID | Requirement |
|---|---|
| INV-030 | A **status strip** shall show: the as-of date in Toronto, the NC clock cutoff and time zone, the IA period status badge (§2.5), and how long ago the NC data was synced. If the last NC sync did not tie out, a red banner shall show the number of findings and link to Settings. |
| INV-031 | **Stock: summary.** Per warehouse class: materials, lines, total quantity. |
| INV-032 | **Stock: by batch** (default). One row per warehouse × material × batch. Columns: warehouse code with class badge, material, batch, supplier batch, production and expiry date, quantity. For today only, also locked, available and stock state. On a past date those columns are greyed out and the page explains why. |
| INV-033 | **Stock: by material.** One row per material. Columns: total, quantity in WMS-managed warehouses, external, production line-side, other (transient, other and unclassified), number of batches, and locked (today only). |
| INV-034 | The Stock tab shall filter by warehouse class (multi-select chips; none selected = all), by material class, and by search (material code, name, local name or batch). It shall sort by material, warehouse, batch or quantity, with server-side paging. |
| INV-035 | For today, the number of rows with zero on-hand but a leftover lock shall be reported, not listed. |
| INV-036 | **Cost tab.** One row per material with non-zero IA quantity or amount. Columns: quantity; amount and unit price (cost viewers only); costing status, giving the number of outbound rows not yet costed in an open period. Tiles show material count, total quantity and, for cost viewers, total amount. Sortable by material, quantity or amount (amount only for cost viewers). |
| INV-037 | Expanding a Cost row shall show (a) the **quantity breakdown by warehouse and batch**, with quantities only, and the page shall state that IA costs at material level only; and (b) the **ledger lines**: period, business date, direction, quantity, amount (cost viewers), transaction type, bill number, source document, batch, warehouse, and estimate flag. Lines are paged. |
| INV-038 | **Ledger tab.** An accounts summary (one line per inventory account, excluding 1471) with quantity and amount (cost viewers), and a separate **1471 impairment** block (or a note that there is no balance). |
| INV-039 | The Ledger table shall show one row per account × material (lines without a material form their own "(no material)" row). Columns: quantity, amount (cost viewers), and the count of unposted lines. Search shall match material code, name or account code. |
| INV-040 | Expanding a Ledger row shall list the voucher lines behind it: year-period, voucher number, prepared date, posted flag, source system, explanation, quantity, amount (cost viewers). Lines are paged. GL lines whose material no longer exists in NC's master shall still be reachable by their internal key and shall not fall into "(no material)". |
| INV-041 | An **Include unposted vouchers** switch (default on) shall apply to the ledger and to its drill-down. |
| INV-042 | All amount fields shall be removed by the server for users without `inventory.cost.view` (§1.2). |

### 3.4 Reconciliation

A four-way comparison for every material with a non-zero figure anywhere on D (about 1,200–1,500 materials). It is computed on request and never stored, because D can be any date.

**Three comparisons**, each between two ledgers that share a dimension:

| Comparison | Measure | Why this pair |
|---|---|---|
| **IC−WMS** | Quantity | WMS only knows its own warehouses, so IC is restricted to WMS-managed warehouses |
| **IA−IC** | Quantity | IA has no warehouse dimension, so IC across all warehouses |
| **GL−IA** | Quantity and amount | GL excludes 1471 and lines with no material |

| ID | Requirement |
|---|---|
| INV-050 | Controls: date (default today, not in the future), material class, search (code or name), sort, status chips (OK / Explained / Unexplained), cause chips, **Expand batches**, and **Include unposted GL vouchers** (default on). |
| INV-051 | **Summary tiles** shall cover all materials and shall not change with table filters: WMS (or "not live" before 2025-02-03); NC stock in WMS warehouses, external, production line-side, and total (the tooltip gives the transient / other / unclassified share); IA quantity and GL quantity, each with its amount for cost viewers; and the count of materials per status. The status counts are clickable as filters. |
| INV-052 | **Table columns:** material (with "Not in NC" and unit-check badges), WMS, NC stock in WMS warehouses, NC stock elsewhere, NC total, IA quantity, GL quantity, IC−WMS, IA−IC, GL−IA quantity, and (cost viewers) IA amount, GL amount and GL−IA amount. Also status, causes, and a **Trace** link. |
| INV-053 | **Attribution.** Each difference shall be explained by deterministic rules (INV-054). The amount a rule explains is subtracted, and what remains is **unexplained**. Status is **Unexplained** if any remainder exceeds tolerance, **Explained** if any cause applied, otherwise **OK**. Tolerances come from Settings (defaults: quantity 0.001, amount 0.01). |
| INV-054 | **Causes implemented:** (table below) |
| INV-055 | IA−IC and GL−IA rules shall each explain **at most the remaining difference, and only in the same direction**. These rules work from cumulative figures that cannot be tied line by line; without the cap they would create differences on materials that agree. |
| INV-056 | Sorting by a difference shall sort by the **unexplained remainder**, largest first. For users without amounts, GL−IA sorts by quantity. |
| INV-057 | Expanding a row shall show its **causes** (label, side, quantity, amount for cost viewers) and, for cost viewers, **GL entries this year not from costing, by source system**. |
| INV-058 | With **Expand batches** on, every material that is tracked by batch shall get its **batch groups** (§2.8). For each group: label, NC batches, WMS lots, supplier batches, match status (both / WMS only / NC only), WMS quantity, NC quantity in WMS warehouses, NC quantity elsewhere, IA quantity and the IC−WMS difference, plus a Trace link for the group. Materials NC stopped tracking by batch shall not expand. GL has no batch and is never broken down. |
| INV-059 | Below the table, two GL blocks shall list figures that cannot sit on a material row: **GL impairment (1471)** and **GL lines without a material**, by account, with quantity and amount (cost viewers). |
| INV-060 | **Export** shall download an Excel workbook of all rows matching the current filters (all pages). Sheets: Materials, Batches (when expanded), GL other, and Info. Causes and status shall be written as text, never as colours. Amounts are included only for cost viewers. |
| INV-061 | Each material row shall show a **unit check** (match / mismatch / unknown) between NC's unit and WMS's base unit. It is information only and never an attribution. |

**INV-054: Causes**

| Cause | Comparison | What it measures |
|---|---|---|
| NC stopped recording this material | IC−WMS | The whole IC−WMS difference of a listed no-batch material on or after its "since" date |
| Material not tracked by batch in NC / not in NC | IC−WMS | The whole difference of a material NC does not know, or one with no NC batch records and no NC stock in WMS warehouses |
| Same total, different batches | IC−WMS | Material totals agree but at least one batch group differs. Computed only with Expand batches on, for all materials, and it then counts in status and filters. |
| Stock in a warehouse NC does not cost | IA−IC | IC quantity in non-costed warehouses (e.g. SC16) |
| Unsigned NC stock documents | IA−IC | Quantity from unsigned IC documents in costed warehouses that IA has not received, excluding stock-state changes |
| Shipped (IC) but not yet invoiced (IA) | IA−IC | Sales shipments out of IC minus sales invoices out of IA |
| IA rows booked in a different period than their date | IA−IC | Rows in IA by period but dated after D, minus rows dated on or before D but booked in a later period |
| Difference carried forward from previous years | GL−IA | GL opening (period 00) of D's year minus IA cumulated to the previous year end, for both quantity and amount |
| GL entries from AP / manual vouchers | GL−IA | This year's GL entries on inventory accounts from sources other than IA and cost management. Cost-management postings are NC's normal flow and are offset by IA, so they are not counted. |

### 3.5 Trace

For one material, and optionally one batch group, Trace shows NC and WMS movements side by side from 2025-02-03 to the end of D. Scope: NC movements in **WMS-managed warehouses only**, against every WMS movement of the SKU. NC's balance before the window is the **opening**. WMS starts from zero. Quantities only.

| ID | Requirement |
|---|---|
| INV-070 | **Entry form.** A material picker searches code, name and local name. Materials with stock today are listed first; materials not in NC and materials NC stopped tracking by batch are badged. An optional batch picker lists the material's **batch groups** with NC and WMS quantity in WMS warehouses today, the number of WMS lots, match status and last movement. Groups with stock come first, then the most recently moved. Picking a batch is optional: empty means the whole material. |
| INV-071 | The page shall reject a future date and a material found in neither NC nor WMS. A batch trace of a material NC stopped tracking by batch (on or after its "since" date) shall be refused with a message to trace the material instead. |
| INV-072 | **Batch scope.** Choosing any NC batch, supplier batch, QC batch or WMS lot number shall trace the whole batch group containing it (§2.8). The page shall show which NC batches and WMS lots make up the group. |
| INV-073 | **Movement categories.** Both sides shall be classified into common categories: opening, manual opening import (WMS), purchase receipt, purchase return, issued to production, returned from production, finished goods received, sales shipment, shipped to external/consignment, department use/sample/scrap/damage, stock count/adjustment, batch or material change, other. NC transfers between two WMS-managed warehouses (e.g. SC11↔SC16) count as **internal moves**, as do WMS location-only moves. NC stock-state changes are **state changes**. Internal moves and state changes are hidden by default and can be shown with a switch. |
| INV-074 | **Pairing**, in tiers. **Tier 1, by document number:** a document reference both sides carry, such as a production order, return number, PO number, delivery-note number, or WMS adjustment/shipment number recorded in NC. References shorter than 6 characters are ignored. One key may join many lines on each side, and the group is compared by its totals. **Tier 2, by quantity and time:** same category, same quantity, nearest in time within a window (7 days by default; 30 days for finished-goods receipts; 120 days for counts/adjustments and manual opening). This is compared per document, because one NC receipt is often many WMS pallets on one inbound document. Lines left over appear on one side only, as **NC only** or **WMS only**, highlighted. |
| INV-075 | **Ordering.** Movements at the same timestamp shall be ordered by **document number** (which reflects creation order), then by line. NC times shall be shown in Toronto time. Date-only NC rows shall show their date only. |
| INV-076 | **Header tiles:** opening (NC), closing on D (NC, WMS, difference, highlighted when non-zero), and movement counts (NC lines, WMS lines, paired by document, paired by quantity, NC only, WMS only). |
| INV-077 | **First divergence.** If the closing balances differ, the page shall identify the first movement after the **last moment the two running balances agreed**, with a rule-built sentence (no AI). It shall also identify the **largest unmatched document since that point**, which is usually the one to investigate. Both are clickable and jump to the row. If the balances agree, the page shall say so. |
| INV-078 | **By category** table: NC total, WMS total and difference per category, in a fixed order. |
| INV-079 | **Movements** table: one row per pair group or unmatched line, in time order. NC lines on the left and WMS lines on the right, each with time, category, document, quantity and that side's running balance. The two sides are shaded differently (NC light blue, WMS light purple) everywhere on the page. The sticky header shows each side's closing balance and the closing difference. Pair badges show "doc &lt;key&gt;" (tier 1) or "qty+time" (tier 2), and each pair group shows its NC−WMS difference. |
| INV-080 | Row details shall show, for NC: transaction type, warehouse (and the other warehouse on a transfer), batch, unsigned flag, WMS reference, source and first documents. For WMS: document type, transaction type, references, lot, QC and supplier batch, reason and user. |
| INV-081 | **Export** shall download an Excel workbook: Summary (opening, each category, closing), Movements (one row per line, in page order, with match state as text and hidden-category rows always included and flagged), and Info. |

### 3.6 Settings

Readable by everyone with `inventory.view`, because the comparison depends on these choices. Every write control appears only with `inventory.admin`, and the page shows "Read-only" otherwise.

| ID | Requirement |
|---|---|
| INV-090 | **NC sync status card:** configured state, last successful sync, last attempt with outcome (running / success / failed with the error), duration, automatic interval and next due time, rows per mirror table, rows dropped as unusable or duplicate, and tie-out result. A **Refresh** button (admin) runs the sync now. A sync already running is refused with a clear message. |
| INV-091 | **WMS history sync card:** the same fields for the WMS transaction-history mirror. The card explains that current WMS stock comes from MRP's WMS sync. |
| INV-092 | **Tie-out findings:** check, measure, label, mirror value, source value, difference, stored up to 500 per check. Findings measured in money are hidden from users without `inventory.cost.view`, and the number hidden is shown instead. |
| INV-093 | **Parameters:** NC sync interval in minutes (0–1440, 0 = off, default 60; it also drives the WMS-history sync); NC time zone (Asia/Shanghai default, or America/Toronto); quantity tolerance (default 0.001); amount tolerance (default 0.01; both 0–1000). Invalid values are rejected. An invalid stored value falls back to its default rather than breaking a page or the scheduler. |
| INV-094 | **NC warehouse classes:** every warehouse NC has, together with every warehouse already classified. Columns: code, name, "Not costed" and "Not in NC" badges, current on-hand, class (WMS-managed / external / production line-side / transient / other), and a note (up to 500 characters). Warehouses without a class are marked **Unclassified** and counted. Unclassified warehouses are never compared with WMS. |
| INV-095 | **Materials not tracked by batch in NC:** material code, since date, note. Add, edit and remove are admin only, and removal is confirmed. Seeded with CF00AF and CF00WT from 2026-01-01. |
| INV-096 | Every Settings change shall record who made it and when. |

---

## 4. Sync schedules & data freshness

### 4.1 Schedules

| Mirror | Owner | Default interval | Where set | Manual refresh |
|---|---|---|---|---|
| WMS current stock (lots, locations) | MRP service | 5 minutes | Portal → Admin → WMS sync | WMS Inventory → Refresh (`inventory.admin` or `mrp.param.write`) |
| NC stock / cost / ledger (IC, IA, GL, plus warehouses, batches, materials, transaction types, IA periods) | Inventory service | 60 minutes | Inventory → Settings → NC sync interval | Settings → NC Refresh (`inventory.admin`) |
| WMS transaction history (movements, lot attributes, documents, codes, SKU units) | Inventory service | Same setting as NC | Same | Settings → WMS Refresh (`inventory.admin`) |

- **Scheduler.** The Inventory scheduler runs inside the Inventory service. It checks every 30 seconds whether each source is due, measured from that source's **last attempt**, so an outage retries once per interval rather than on every check. NC and WMS are checked separately: an outage on one does not hold back the other. One database lock prevents two syncs running at once, whether scheduled or manual.
- **Development.** Development environments switch the scheduler off with a deployment setting and use the Refresh buttons instead.
- **Interval changes** take effect on the next check, without a restart.

### 4.2 How a sync works

- **Full replacement.** Each sync extracts everything for its slice from the source, then replaces the whole mirror in **one database transaction**. Readers see either the previous snapshot or the new one, never a mix. A failed extract leaves the previous snapshot in place and records the error.
- **Nothing dropped silently.** Deleted rows in NC, cancelled WMS transactions, WMS transactions with no quantity effect, location-only transfers, rows missing a key, and duplicate keys are all **counted** and shown in Settings.
- **Tie-out.** After every sync, the new mirror is checked against figures the source computed itself, fetched in the same session:

  | Check | Mirror | Compared against | Tolerance |
  |---|---|---|---|
  | IC | Sum of all stock movements per warehouse × material × batch | NC's current on-hand | Quantity 0.0001 |
  | IA | Ledger summed to the last closed period, per material | NC's month-end balance | Quantity 0.0001, amount 0.005 |
  | GL | Posted lines per year × account | NC's GL balance | Quantity 0.0001, amount 0.005 |
  | WMS | Movements summed per SKU × lot | WMS's current lot quantity | Quantity 0.0001 |

  A tie-out mismatch does **not** fail the sync, because the data is still the best copy available. The result is recorded and the NC page shows a red banner. No Task or notification is raised (business decision).

### 4.3 Freshness on screen

- **WMS Inventory** shows the snapshot age (INV-010).
- **NC Inventory** shows "NC data synced N min ago" and the tie-out banner (INV-030).
- **Reconciliation and Trace** read the WMS-history mirror for every WMS figure, including today. On today's date, WMS figures on these pages can therefore be up to one Inventory sync interval old, and can differ slightly from WMS Inventory, which reads MRP's more frequent snapshot.
- **Observed at production go-live (2026-10-15):** the NC sync took about 56 seconds and tied out clean. The WMS-history sync took about 11 seconds, with only the two known WMS-side exceptions (§2.9).

---

## 5. Non-functional requirements

| Area | Requirement |
|---|---|
| **Read-only towards sources** | All NC and WMS access is plain SELECT through read-only accounts. NC uses the standard connection settings shared with the other services. WMS requires the Oracle client library because Flux runs an older Oracle version. One connection mode applies per service process, so the NC and WMS syncs can run in the same process. |
| **Security** | Every endpoint requires a valid UniOps access token and `inventory.view`. Writes require `inventory.admin`. Amounts are stripped on the server (§1.2). Sort orders and filters are whitelisted, and all user input is bound as parameters. API documentation endpoints are off in production. |
| **No side effects** | No Tasks, emails or notifications. No writes to NC, WMS, MRP or Finance data. |
| **Performance** | Reconciliation computes all materials for D in one request (a handful of aggregate queries over the mirrors) and filters and pages in memory. This fits the current scale of about 1,500 materials. Batch groups, when requested, are computed for all materials so that the batch-label cause counts consistently. If this becomes slow, results can be cached per date. |
| **Accuracy** | Decimal arithmetic end to end, with amounts never passed through floating point except in the Excel export. Units of measure are never summed silently. Date-only NC rows are compared by date (§2.7). |
| **Auditability** | Every sync records its time, duration, row counts, dropped-row counts, outcome, error and tie-out findings. Every Settings change records the user and time. |
| **Availability** | If NC or WMS is not configured on a server, Refresh returns "not configured" and the scheduler skips that source. Pages keep showing the last snapshot. |
| **Deployment** | Two images, the Inventory web front end and the Inventory API, behind their own HTTPS host names. They follow the standard UniOps release process. Database migrations are additive. |
| **UI** | English only. Fits the UniOps shell (multi-tab, collapsible sidebar, mobile menu). Tables keep their header visible while scrolling. |
| **AI assistant** | The module is **not yet covered** by the AI assistant's guide layer. The business deferred this until the module's features are complete (§6). |

---

## 6. Open Items

| ID | Item | Status |
|---|---|---|
| INV-O01 | Permissions are granted to `system_admin` only. The business must assign `inventory.view`, `inventory.cost.view` and `inventory.admin` to the appropriate roles in Portal → Access Control before other users can use the module. | Open |
| INV-O02 | **Trace balance chart.** The design calls for two running-balance lines (NC and WMS) over time, with the first divergence marked, at the top of Trace. The page currently shows tiles, the divergence sentence and the tables only. | Not implemented |
| INV-O03 | **Timing-difference cause** (IC−WMS on today's date: NC movements between the two sources' sync times). | Not implemented |
| INV-O04 | **Uncosted-period cause** (GL−IA in an open IA period). It is flagged in the NC page's period badge and the Cost tab, but not quantified as a cause in Reconciliation. | Not implemented |
| INV-O05 | **Unclassified warehouse as a named cause.** Unclassified warehouses are flagged in Settings and counted in the NC total under "other", but Reconciliation does not name them as a cause. | Not implemented |
| INV-O06 | **Drill-down from a cause to its documents**, and links from Trace document numbers to the source document, as described in the design. Today a cause shows its quantity and amount only, and document numbers are plain text. | Not implemented |
| INV-O07 | **WMS current-stock sync** still lives in MRP (business decision for phase 1). Plan: move it into Inventory and have MRP read from there. | Open |
| INV-O08 | **AI assistant guide layer and ontology** for this module. The business deferred this to a separate effort once the module is complete; this is an explicit exception to the rule that every change updates the guide layer. | Open |
| INV-O09 | **NC time zone.** That NC writes China time was inferred from data and adopted by business decision. It should be confirmed with the NC administrator. The Settings switch exists for this reason. | Open |
| INV-O10 | **Unexplained differences at go-live.** In the 2026-08-31 trial run, 2 materials remained unexplained on IA−IC, 7 on GL−IA (e.g. CF0086, matching the manual review), and 137 on IC−WMS, mostly packaging such as materials WMS holds but NC does not. These are business findings for inventory control to work through, not system defects. | Open |
| INV-O11 | The two known WMS-side tie-out exceptions (CR0241, FULLCARTON) appear on every WMS-history sync. Correcting them is a WMS data task. | Open |
| INV-O12 | **WMS Inventory unit shown as "mixed".** When none of a batch's lots records a unit, the batch's unit shows as "mixed" (implying the lots contradict each other) instead of "not recorded". A fix exists on an unmerged branch and is not on main. | Defect |
| INV-O13 | **NC Cost and Ledger accept a future date** through the API. The Stock tab, Reconciliation and Trace reject it, and the page's date picker prevents it, so the effect is limited to direct API calls. | Defect |
| INV-O14 | Old bookmarks to MRP's former inventory page now land on an unknown route in MRP rather than redirecting to Inventory → WMS Inventory. | Defect |
| INV-O15 | The WMS history start (2025-02-03) is fixed in code. It is not a setting by design, but a change would require a release. | Open |
