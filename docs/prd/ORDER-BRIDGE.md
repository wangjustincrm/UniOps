# Order Bridge — Product Requirements Document

- **Version:** 1.0
- **Date:** 2026-10-16
- **Status:** Proposed (in team review)
- **Scope:** Rebuild the Zoho Creator "Order Bridge" order hub (Shopify and retailer EDI orders, Radiant 3PL EDI, retailer invoicing, lot-level inventory) as a new UniOps module, and retire Zoho One.
- **Source:** Order Bridge PRD (English edition of the trilingual PRD issued 2026-10-09, based on the complete exported Zoho application script), plus the team review comments collected on the PRD review board through 2026-10-15.

### What changed in this version

- First entry of Order Bridge in the UniOps PRD set. The analysis of the current Zoho system and the proposed rebuild are carried over from the 2026-10-09 PRD without change.
- Requirements are restated as numbered functional requirements (FR-OB-xx).
- The 39 team review comments received on the review board (2026-10-13 to 2026-10-15) are **not yet applied** to the requirements. They are summarised in **Open Items** together with the open questions from the original PRD, for the product owner to accept or reject.

---

## 1. Background and goals

Order Bridge is the company's in-house order hub, built on Zoho Creator. It:

- Pulls Shopify web-store orders and retailer EDI orders into a single order table.
- Sends those orders to the third-party warehouse, Radiant, for shipment.
- After Radiant confirms shipment, writes fulfillment back to Shopify and invoices the retailer.
- Keeps a lot-level inventory ledger alongside.
- Keeps a file register, a scheduler run log, function logs and a health-check page.

**Goal:** retire Zoho One and rebuild these functions 1:1 in UniOps without interrupting the business, fixing the defects identified in the current system.

The analysis is based on the complete application script exported from Zoho Creator on 2026-10-09 (about 17,700 lines: 18 forms, 37 reports, 4 pages, 95 functions, one WorkDrive connection), not on screenshots or verbal descriptions.

### 1.1 In scope

- Inbound orders: Shopify Admin API (JSON) and retailer 850 purchase orders (via TrueCommerce).
- Unified order records, line items, status tracking, holds and exception review.
- EDI with Radiant: 940 shipping order, 943 transfer, 945 shipment confirmation, 944 receipt confirmation, 997 functional acknowledgement.
- Post-shipment write-back: Shopify fulfillment and tracking; 810 invoices to retailers (EDI, email or PDF).
- Lot-level inventory: on hand, reserved, in transit, available; manual adjustments and reversals.
- File register, scheduler run records, logs, operations health page.

### 1.2 Out of scope

- Accounting entries, receivables and cash collection. NC remains the financial source of truth; Order Bridge only produces invoice files (see FINANCE.md).
- Configuration of Shopify, TrueCommerce and Radiant themselves.
- Generic Zoho platform features (form builder, Zoho user system). UniOps uses its own framework (see PORTAL.md).

## 2. System overview

Order Bridge connects to three external systems:

| System | Role | Connection today | Connection in UniOps |
| --- | --- | --- | --- |
| Shopify | Web-store orders in; fulfillment and tracking out | Direct Admin API | Direct Admin API |
| TrueCommerce | Retailer 850s in (as JSON); 810 invoices out | SFTP via the third-party gateway ftp-api.com | Direct SFTP |
| Radiant (3PL) | 940/943 out; 997/945/944 in | SFTP via ftp-api.com | Direct SFTP |

Raw files are archived in Zoho WorkDrive today; notifications go out by email. All processing is driven by a "Scheduler" page that runs one cycle each time it is opened.

## 3. Current system: data model

None of the 18 Zoho forms declare required fields, unique constraints or validation. Deduplication, validation and status changes all live in functions, and natural keys are kept unique only by "look up, then insert". The rebuild adds unique indexes and closed enumerations.

| Domain | Table | Purpose | Natural key |
| --- | --- | --- | --- |
| Orders | Orders | Order header (retailer 850 / Shopify / manual) | Order number (autonumber from 1,000,000); source key = partner + source order number |
| Orders | Order Items | Order lines | Order + line number |
| Orders | Order Fulfillment Lots | Lots, expiry dates and tracking numbers from 945s | — |
| Files | File History | Register of every inbound/outbound file, with processing, sending and ACK status | File name |
| Transfers | Transfer Orders / Items / Receiving Items | Warehouse transfer (943 → 997 → 944) and its receipt lines | Transfer number (from 100) |
| Inventory | Inventory Balance / Item Balance | Balance snapshots per warehouse-item-lot and per item | Balance key |
| Inventory | Inventory Adjustments | Manual adjustments; posted and reversible | Adjustment number |
| Master data | Item Master, Warehouses, Partner Config, Retailer DC Routing | Items, warehouses, trading partners and invoice methods, Ship-To GLN → DC codes | Item code, warehouse code, partner code + source system, destination code |
| Config | Connection Config, Settings | SFTP/API connections; key-value settings | Name |
| Runtime | Scheduler Run, Logs | Scheduler runs; function logs | — |

### 3.1 Key order fields

- **Source:** source type (Retailer / Shopify / Manual), partner, source order id and number, order type, department number, Shopify financial and fulfillment status.
- **Dates:** order date, requested delivery date, delivery date, cancel date, fulfillment hold-until (delays the 940).
- **Addresses:** ship-to (name, GLN code used for DC routing, structured address), bill-to, contact.
- **Amounts:** currency (CAD/USD), order total, invoice total.
- **Four status lines:** order state, order status, fulfillment status, invoice status (values in section 9), plus hold reason, cancel reason and cancellation time.
- **Print templates:** internal audit packet, invoice, purchase order, packing list (with lots, expiry and tracking).

Order lines carry ordered quantity and unit of measure (EA / case / can), unit price, case pack, the warehouse quantity (always EA), shipped/cancelled/backordered quantities, line status, the invoice view (delivered quantity, invoice unit price and amount) and review flags.

### 3.2 File register

Each file record holds its identity (name, document type, direction, system, partner, archive reference), EDI control numbers (ISA, GS, ST), three statuses (processing, file, ACK), retry and error details, and a snapshot of the partner's ACK and invoice settings.

### 3.3 Inventory quantities

Both balance tables hold on hand (labelled "Preparing for Fulfillment" in the UI), reserved, in transit and available, all in EA. Both are recalculated snapshots; there is no movement table, and adjustments are the only explicit transactions (Draft → Posted → Reversed).

### 3.4 Current seed data

**Trading partners (7, all active):**

| Partner | Abbrev. | Channel | Partner code | 940 delay (min) | Invoice method |
| --- | --- | --- | --- | --- | --- |
| Shopify | SHP | Shopify | Shopify | 10 | None |
| Manual | MAN | Manual | Manual | 0 | None |
| Sobeys | SOB | TrueCommerce | Sobeys | 0 | 810 JSON |
| Jean Coutu | JC | TrueCommerce | JeanCoutu | 0 | Email (to an internal employee, forwarded by hand) |
| Loblaws | LCL | TrueCommerce | AC-9898 | 0 | 810 JSON |
| Walmart Canada | WM | TrueCommerce | Walmart | 0 | 810 JSON |
| Overwaitea Food Group | OFG | TrueCommerce | OverwaiteaFood | 0 | 810 JSON |

- **Items (5, all 6 cans per case):** CF0092 NIURISS Stage 1, CF0093 NIURISS Stage 3, CF0102 NIURISS Smart 1, CF0103 NIURISS Smart 2, CF0110 Walmart Parents Choice 700 g.
- **Warehouses (3):** CRM (Canada Royal Milk ULC), CLN02 (Radiant Coleraine, default inventory warehouse), MANUAL.
- **Connections (3):** Radiant SFTP, TrueCommerce SFTP, Shopify Admin API.
- **DC routing:** 457 rows, all Walmart, covering 57 Walmart DCs; no other retailer has routing data.

### 3.5 Data-model problems to fix in the rebuild

1. The four order status lines overlap; one must be the source and the rest derived.
2. Most picklists accept values outside the list.
3. The file register's partner field is a static list that does not match the partner table.
4. Some field names contradict their UI labels (on-hand shown as "Preparing for Fulfillment"; delivered quantity shown as "Quantity In").
5. Adjustments have no warehouse; line numbers are text (10 sorts before 2); quantities mix integers and decimals.
6. Re-running the seed overwrites configuration edited in the UI; Radiant's seeded paths point to test directories.
7. Two retry settings are never read; one setting read by code is never seeded.
8. CF0110's UPC has 11 digits; every item but CF0093 shares the same gross weight (likely a placeholder).
9. **Security:** SFTP passwords and the Shopify token are stored in plain text in the seed function, the connection table and the export file.

## 4. Order source 1: Shopify

Shopify orders are pulled incrementally from the Admin API, at most 20 per run, deduplicated by Shopify order id (new id creates, known id updates). Paid, unfulfilled orders get a 940 sent to Radiant after a configured delay. After shipment, fulfillment and tracking are written back to Shopify.

### 4.1 Current behaviour

- **Pull:** one page of up to 20 orders updated since a stored cursor (empty cursor = now minus 72 hours); no pagination. The cursor advances to the last order's update time, or by one minute if unchanged, and only after the raw page is archived and registered.
- **Header mapping:** Shopify id → source order id (dedup key); order name → source order number; currency (default CAD); created time → order date (time-zone offset dropped); financial and fulfillment status; cancellation; shipping method (contains "express" → EXP, otherwise GND); customer id → ship-to/bill-to code; shipping and billing address (billing replaced by shipping if missing or outside Canada); contact; note. Shipping, tax and discounts are not read; order total = merchandise value before discounts.
- **Lines:** every line item is taken (gift cards, non-shipping and refunded lines are not filtered). Variant title conversion to cans: contains "Case" → 6; "2-Can" → 2; "Can" → 1; otherwise none. Item matching by GTIN → UPC → SKU → item code; an unmatched line is created with no item and no alert.
- **Status at creation (first match wins):** order number below 9000 → ignored; cancelled → Cancelled; unpaid (including "authorized") → On Hold / Payment Required; already fulfilled in Shopify → Completed, no 940; otherwise Active / Pending with the 940 after the partner's delay.
- **Write-back:** when Radiant's 997 accepts the 940, a fulfillment is created without tracking and without notifying the customer; when the 945 brings a tracking number, tracking is added and the customer is notified. The carrier is forced to Purolator.

### 4.2 Known Shopify defects (fix in the rebuild)

- Orders put on hold for payment are not released when paid (operator-precedence bug); release is manual today.
- A Shopify cancellation only changes the order state; a 940 already sent is not recalled and the warehouse is not told.
- Every update soft-deletes and rebuilds all lines, even for shipped orders, and the system's own write-back triggers this on the next pull.
- Time-zone offset hard-coded to -04:00 (wrong in winter); more than 20 orders with the same update time are skipped.
- Multi-location orders only fulfill the first fulfillment order.

## 5. Order source 2: retailer EDI via TrueCommerce

TrueCommerce converts each retailer 850 to JSON on its SFTP; Order Bridge pulls it. To invoice, Order Bridge uploads an 810 JSON, which TrueCommerce converts and sends. No 855 or 856 is exchanged.

### 5.1 Current behaviour

- **Pull:** the whole export directory is downloaded each run; dedup key = original file name + remote modified time + system; remote deletion is optional per system.
- **Type detection by content:** JSON with a purchase-order key → 850; JSON with an invoice key → 810 (skipped inbound); X12 → by transaction set; anything else rejected.
- **Partner identification:** the file's trading-partner code must match an active TrueCommerce partner, otherwise the file fails. Live retailers today: Walmart Canada, Loblaws, Sobeys, Jean Coutu; Overwaitea configured.
- **850 mapping:** PO number (dedup key), PO date, requested delivery date (falls back to do-not-deliver-after), cancel date (stored, does not cancel), order type (Walmart type 0020 adds an MR reference on the 940), department number, ship-to (GLN used for DC routing), bill-to, buying-party contact, notes. Currency is hard-coded to CAD. Ship dates, routing, SCAC, terms and mark-for are parsed and dropped.
- **Lines:** warehouse quantity is always quantity × inner packs (default 6), regardless of unit — an EA order would be multiplied by 6.
- **Creation rules:** partner + PO number is the dedup key (a resend updates the order and rebuilds lines); only the first PO in a file is processed; a cancellation 850 is treated as an update; initial status Received / Active / Pending; invoice status Not Required or Pending by invoice method; no hold or review gate for unmatched items or zero quantities.
- **DC routing (940 only):** ship-to GLN → DC number (LO code); label code UCC_CRM_WMT for Walmart, otherwise UCC_CRM_LBW. Non-Walmart lookups send an empty LO reference.
- **810 invoicing:** after the 945, the order waits for a **manually entered delivery date** (today or earlier), which also becomes the invoice date. Then by invoice method: 810 JSON → generated and uploaded, order closed on success; Email/PDF → order closed directly; None → closed as Not Required; anything else → Failed / On Hold. Each invoiced order also emails PO, invoice and packing-list PDFs to finance AR and the partner's invoice recipient. Invoiced quantities and amounts are written back to the lines.

## 6. Radiant 3PL: EDI and inventory

Five X12 message types are exchanged over Radiant's SFTP.

| Message | Direction | Trigger | Needs 997 |
| --- | --- | --- | --- |
| 940 shipping order | Out | Order Active, fulfillment Pending, delay elapsed | Yes |
| 943 transfer | Out | Transfer manually set to Ready To Send | Yes |
| 997 functional ack | In | Acknowledges a 940 / 943 | — |
| 945 shipment confirmation | In | Order shipped | — |
| 944 receipt confirmation | In | Transfer received | — |

### 6.1 Current behaviour (summary)

- **Envelope:** fixed sender/receiver identifiers, production indicator, ISA version 00401 with GS version 006030 (mismatch, to confirm with Radiant); the 943 uses the same functional-group code as the 940.
- **Control numbers:** ISA, GS and ST control numbers all reuse a zero-padded business number (order number for the 940, transfer number for the 943). Resends reuse the control number, and an order and a transfer with the same number collide.
- **940 content:** warehouse order header with internal and retailer numbers; fixed shipper and warehouse parties; ship-to and bill-to from the order; references (label code, DC number, Walmart order type, department) for retailer orders; requested-delivery and current dates; carrier (Purolator ground/express for Shopify, LTL/WIFS for retailers); lines in EA; total quantity and weight (with hard-coded fallback weights). A 940 is not regenerated while a live one exists.
- **997:** matched by group control number; only the overall result is read. Accepted → Shopify fulfillment created and order waits for the 945; rejected → On Hold with a failure email. Resending after rejection is manual. No ACK timeout logic exists.
- **945:** order matched by warehouse order number (several fallbacks); lines by line number then by item identifiers; lot, expiry, tracking (one per file) and ship date written. Re-parsing the same file is idempotent, but the same shipment in a new file is booked twice. The first 945 marks the whole order shipped; lot exceptions are recorded but immediately overwritten.
- **943 / 944:** the 943 carries hard-coded addresses and no lot numbers. The 944 receipt lines are allocated to transfer lines by line number, then lot, then item; anything unallocatable becomes a discrepancy. Every 944 replaces all receipt lines (incremental 944s would lose data).
- **Inventory:** **balances are not updated automatically** — incremental recalculation has been disabled and the full recalculation has no entry point. On 2026-10-09 the live page showed available stock of -42,974. "On hand" actually means quantity on 940s sent but not yet shipped; reserved comes only from manual adjustments; there is no opening-stock concept; pending shipments are booked on a "no lot" bucket that stays negative. The recalculation lock is not atomic.

## 7. Current automation, notifications and logs

- **Scheduling:** Zoho has no scheduled tasks. One receive-and-send cycle runs each time the Scheduler page is opened. A cycle has a budget of 10 processing units and runs fetch → parse inbound (only 2 files per cycle) → prepare outbound (940s, 943s, invoices) → send. A backlog of inbound files blocks all outbound; one failing 940 stops the cycle; there is no re-entry lock; the run log is never written.
- **Retries:** none automatic. A manual resend function exists but has no UI entry point; the retry settings are never read; failed inbound parses need a manual status reset.
- **Email notifications:** order documents to finance AR and the partner on invoicing; file failure and integration error emails to a single error address. **No notification** is sent for 850 or Shopify parse failures, SFTP fetch failures, send failures or 940 generation failures (62 failed inbound files had accumulated by 2026-10-09).
- **Logs:** only three code paths write logs; warnings count as success.
- **Developer-only functions:** full inventory recalculation, manual adjustment, posting/reversing adjustments, resending files, resending invoice emails, a carrier data-fix script.

## 8. Current pages and reports

- **Order Dashboard:** 10 metric cards (on 2026-10-09: 19 active, 0 on hold, 8,000 completed — possibly a count cap, 350 cancelled, 66 failed files, 17/2/0 open retailer/Shopify/manual, 8 waiting on warehouse, 12 invoice work) and 8 embedded lists.
- **Operations Health:** read-only diagnostic on every load (PASSED / WARNING / FAILED) with 10 count cards and lists of critical issues, warnings and recommended actions. Result on 2026-10-09: WARNING with 6 warnings.
- **Inventory & Transfers Dashboard:** 5 cards and two lists (by item, by lot); despite the name, nothing about transfers.
- **Scheduler:** empty page that runs a cycle when opened.
- **Reports:** 33 flat list reports (orders, lines, lots, transfers, inventory, file register, logs, settings). The connection report shows passwords in plain text.
- **Print templates:** audit packet, invoice, purchase order, packing list, transfer order (external and internal).
- **Permissions:** none — the application is effectively single-user; any user can see credentials and change any status, with no audit trail.

## 9. Order lifecycle and state machine

A normal order passes through eight stages: Received → Preparing Warehouse Request → Waiting for Warehouse ACK → Waiting for Warehouse Confirmation → Waiting for Delivery (retailer orders only, waiting for the manual delivery date) → Waiting for Invoice → Invoice Queued → Completed, with On Hold and Cancelled as exception states. Shopify orders (invoice method None) complete right after shipment.

| Status line | Values |
| --- | --- |
| Order state | Active, On Hold, Completed, Cancelled |
| Order status | Received, Preparing Warehouse Request, Waiting for Warehouse ACK, Waiting for Warehouse Confirmation, Waiting for Delivery, Waiting for Invoice, Invoice Queued, Completed, Cancelled |
| Fulfillment status | Pending, 940 Generated, 940 Sent - Waiting for ACK, Waiting for 945, Confirmed, Rejected, Timeout, On Hold, Review Required, Failed (code also writes Payment Required, Fulfilled, Cancelled) |
| Invoice status | Not Required, Pending, Waiting for Delivery Date, Ready, Queued, Sent, Failed |
| Transfer status | Draft → Ready To Send → 943 Generated → Waiting for 997 → Waiting for 944 → Received / Partially Received / Discrepancy; Failed on 997 rejection |
| File processing / ACK | Pending → Processing → Processed / Failed / Skipped (inbound); Ready To Send → Sent / Failed (outbound); ACK Not Required / Waiting / Accepted / Rejected |

Values defined but never written (ACK Timeout, 943 Sent, 997 Accepted, transfer Cancelled) must be implemented or removed. **Proposed for UniOps:** the fulfillment and invoice lines are the source; order state is derived from them.

## 10. Requirements for the UniOps rebuild

Business behaviour follows sections 4–9 1:1 except where a requirement below fixes a defect. Cutover is channel by channel, not all at once.

### 10.1 Architecture and placement

| ID | Requirement |
| --- | --- |
| FR-OB-01 | Order Bridge is a new standalone UniOps module with its own back-end service and database schema and its own front end, reachable from the Portal (PORTAL.md). |
| FR-OB-02 | Item data (SKU, UPC, GTIN, case pack, gross weight) comes from UniOps master data; case pack is taken from item-level unit-of-measure conversion, not a hard-coded 6. |
| FR-OB-03 | Trading partners, DC routing and warehouses are module configuration tables; DC routing references the partner directly (no fuzzy name matching). |
| FR-OB-04 | SFTP and Shopify credentials are held as server secrets, never in the database or the UI. SFTP is connected directly; the ftp-api.com relay is dropped. |
| FR-OB-05 | Raw inbound and outbound files are archived in the UniOps file store, replacing WorkDrive. |
| FR-OB-06 | Exceptions create owned tasks in the UniOps Task Inbox (with email through the shared outbox), not just emails. |
| FR-OB-07 | Scheduling runs as a background job inside the module, with a mutual-exclusion lock and a run record per cycle; opening a page never triggers processing. |
| FR-OB-08 | Unique indexes and closed enumerations replace "look up, then insert" and open picklists. |

### 10.2 Inbound orders

| ID | Requirement |
| --- | --- |
| FR-OB-10 | Pull Shopify orders incrementally with pagination and correct time-zone handling, with no order skipped when many share an update time. |
| FR-OB-11 | Read Shopify shipping, tax and discounts; use the actual carrier; support multiple fulfillment orders. |
| FR-OB-12 | Release Shopify orders held for payment automatically once paid. |
| FR-OB-13 | Pull TrueCommerce 850 JSON files directly over SFTP; process every PO in a file, not only the first. |
| FR-OB-14 | Convert 850 quantities using the actual ordered unit of measure; all internal quantities are held in EA and converted only at message boundaries. |
| FR-OB-15 | Unmatched items, zero quantities and unknown partners put the order into review and raise a task; nothing is created silently. |
| FR-OB-16 | Once a 940 has been sent, source updates and cancellations (Shopify or 850) no longer rebuild order lines; they create a "source change to review" task. |
| FR-OB-17 | Retain the "ignore historical Shopify orders" cut-off only if confirmed (see Open Items OB-OI-25); the original PRD proposed removing it. |

### 10.3 Radiant EDI

| ID | Requirement |
| --- | --- |
| FR-OB-20 | Reproduce the 940 and 943 envelopes, segment structure and field values exactly (including label, DC, order-type and department references and carrier codes). Before cutover, compare old and new files for the same order byte for byte. |
| FR-OB-21 | Keep the 810 JSON structure, the 945/944/997 parsing positions and the file-naming rules unchanged. |
| FR-OB-22 | Allocate control numbers from an incrementing counter per partner and document type, never reused on resend; normalise zero-padding when matching 997s; start above the highest number used by Zoho. |
| FR-OB-23 | Mark an outbound file Timeout when no 997 arrives within a configured time, and raise a task. |
| FR-OB-24 | Retry failed sends automatically with a configured count and interval. |
| FR-OB-25 | Make 945 processing idempotent per shipment/tracking number; partial shipments do not mark the whole order shipped; lot exceptions block invoicing. |
| FR-OB-26 | Read inbound X12 separators from the ISA segment instead of hard-coding them. |
| FR-OB-27 | Move hard-coded values (company and warehouse addresses, SCAC, label codes, fallback weights, invoice letterhead) into configuration. |

### 10.4 Invoicing

| ID | Requirement |
| --- | --- |
| FR-OB-30 | Invoice by each partner's configured invoice method (810 JSON, email/PDF, none), unchanged from today. |
| FR-OB-31 | Provide a screen to enter delivery dates in bulk and raise to-do tasks for shipped orders awaiting a delivery date (automatic capture only if a source is found). |
| FR-OB-32 | Keep the finance document email (PO, invoice, packing list) to finance AR and the partner's invoice recipient; all user-supplied text is escaped in emails. |

### 10.5 Inventory

| ID | Requirement |
| --- | --- |
| FR-OB-40 | Replace balance snapshots with a movement ledger: every receipt, shipment, reservation and adjustment writes a signed movement; balances are summed from the ledger. |
| FR-OB-41 | Redefine quantities as on hand, allocated, in transit and available; support opening stock. |
| FR-OB-42 | Manual adjustments carry a warehouse and follow Draft → Posted → Reversed, with reversal creating an offsetting movement. |
| FR-OB-43 | Allow an authorised user to re-run a full inventory re-evaluation from transfers and Radiant fulfilments (see Open Items regarding deferral). |

### 10.6 Reliability, visibility and audit

| ID | Requirement |
| --- | --- |
| FR-OB-50 | One failing order or file never blocks others; remove the "2 inbound files per cycle" limit and the "inbound backlog stops outbound" rule. |
| FR-OB-51 | Every failure (850 / Shopify parse, SFTP fetch, send, 940 generation) creates a task and appears on the health page. |
| FR-OB-52 | Provide an Operations Health page equivalent to today's diagnostic, plus Order and Inventory dashboards with the same metric definitions. |
| FR-OB-53 | Record every cycle, every function failure and every user action on orders (status change, hold release, delivery date entry) with user and time. |
| FR-OB-54 | Replace developer-only functions (recalculation, adjustments, resend file, resend invoice email) with permission-controlled UI actions. |
| FR-OB-55 | Provide the existing print templates (audit packet, invoice, purchase order, packing list, transfer documents). |

### 10.7 Roles and permissions

Which people hold each permission requires business confirmation.

| ID | Permission | Covers | Suggested holders |
| --- | --- | --- | --- |
| FR-OB-60 | Read | View orders, files, inventory, dashboards | Sales, logistics, finance |
| FR-OB-61 | Order operate | Release holds, enter delivery dates, re-queue 940s, cancel orders | Order operations |
| FR-OB-62 | File retry | Resend or re-parse files, mark as ignored | Order operations, IT |
| FR-OB-63 | Transfer manage | Create transfers and send 943s | Logistics |
| FR-OB-64 | Inventory adjust | Adjustments, posting, reversal, full recalculation | Logistics supervisor |
| FR-OB-65 | Invoice manage | Invoicing, resending invoice emails | Finance AR |
| FR-OB-66 | Configuration | Partners, items, DC routing, connections | IT / system administrator |

## 11. Cutover plan

1. **Data migration:** export all orders, lines, lots, transfers, file register, partners, DC routing and settings from Zoho; move raw files from WorkDrive into the UniOps file store. Historical orders become read-only; in-flight orders migrate with their status.
2. **Shadow run:** UniOps pulls the same files and generates 940s and 810s without sending, compared order by order against Zoho until a full week has zero differences.
3. **Channel-by-channel switch:** Shopify → retailers → transfers. For each channel, stop Zoho's fetch and send so only one system sends 940s; hand over the Shopify cursor; start control numbers in a new range.
4. **Hypercare:** at least one full ship-to-invoice cycle, with Zoho kept read-only for reconciliation.
5. **Retire Zoho:** rotate SFTP passwords and the Shopify token; remove any credentials the relay may hold.

Any live file sent to Shopify, retailers or Radiant during testing or shadow running requires explicit approval.

## 12. Related documents

- FINANCE.md — NC remains the financial source of truth; invoices only.
- INVENTORY.md — UniOps inventory views (Order Bridge lot ledger is separate until aligned).
- PORTAL.md — module entry, permissions and Task Inbox.
- EPMS.md, MRP.md — item and unit-of-measure master data.

## 13. Open Items

Items OB-OI-01 to OB-OI-20 are the open questions from the original PRD. Items OB-OI-21 onwards summarise team review comments (2026-10-13 to 2026-10-15); all are recorded as open on the review board and **have not been applied** to the requirements above. Comments are attributed where the author gave a name.

| ID | Item | Status |
| --- | --- | --- |
| OB-OI-01 | Who opens the Zoho Scheduler page, with what tool and how often? (Review comment, Amir: it is called externally every 5 minutes.) | Answered in review; to confirm |
| OB-OI-02 | Since when has incremental inventory recalculation been disabled, when did the full recalculation last run, and is anyone using the inventory figures? | Open |
| OB-OI-03 | Actual effect of the ambiguous operator precedence in Zoho (A && B \|\| C). (Review comment, Amir: implement as A && (B \|\| C).) | Answered in review; to confirm |
| OB-OI-04 | Completed count of exactly 8,000: real total or a count cap? | Open |
| OB-OI-05 | Production Radiant SFTP paths. (Review comment, Amir: /prod/in and /prod/out, to be held in configuration.) | Answered in review; to confirm |
| OB-OI-06 | Who enters the delivery date today and from what source? (Review comments, Amir: entered manually; no automatic source exists; at minimum notify a user to update it.) | Answered in review; to confirm |
| OB-OI-07 | Who releases paid Shopify orders from hold today? | Open |
| OB-OI-08 | Should Jean Coutu's invoice email go directly to the retailer? (Review comment, Amir: where a retailer has an invoice email configured, send to it and copy the internal employee.) | Answered in review; to confirm |
| OB-OI-09 | How do sales orders and invoices reach NC — manual entry or an interface? Decides whether UniOps must push orders to NC. | Open |
| OB-OI-10 | Does any retailer order in EA? (Review comment, Amir: all Radiant messages are in EA — retailer 850 units not yet confirmed.) | Partly answered |
| OB-OI-11 | Difference between unit price and invoice unit price; is the invoice edited by hand? (Review comment, Amir: users need to be able to edit invoices.) | Answered in review; to confirm |
| OB-OI-12 | Correct CF0110's 11-digit UPC and the placeholder gross weights? (Review comment, Amir: the item master must be a maintainable table — add and edit.) | Open |
| OB-OI-13 | DC routing for non-Walmart retailers and Overwaitea configuration. (Review comment, Amir: DC data is mandatory for Walmart only; may be ignored otherwise; keep in configuration.) | Answered in review; to confirm |
| OB-OI-14 | Are Radiant 944s cumulative or incremental? (Review comment, Amir: Radiant sends one file per transaction; each 940 is one order.) | Partly answered |
| OB-OI-15 | Are 945 quantities in EA or cases, and which date is the authoritative ship date? (Review comment, Amir: all Radiant exchanges are in EA.) | Partly answered |
| OB-OI-16 | ISA/GS version mismatch and 943 functional-group code: what does Radiant require? (Review comment, Amir: EDI issues may be corrected; EDI accuracy is critical because wrong data or format can cause large losses.) | Open — confirm with Radiant |
| OB-OI-17 | Which carrier is actually used (Purolator vs Canada Post)? | Open |
| OB-OI-18 | Obtain the full 850 and 810 JSON schema and samples from TrueCommerce. | Open |
| OB-OI-19 | Will Radiant accept a new control-number range? (Review comment, Amir: no limit or issue with control numbers.) | Answered in review; to confirm |
| OB-OI-20 | Rotate SFTP passwords and Shopify token now; establish who runs ftp-api.com and whether it retains customer data. (Review comments, Amir: no need to change passwords now; ftp-api.com was only used because of a Zoho limitation and can be dropped.) | Decision needed — review comment conflicts with the PRD's security recommendation |
| OB-OI-21 | 940 delay: review comment (Amir) states Shopify is 3 hours and other partners 1 hour, versus 10 and 0 minutes in the seed data. Confirm production values. | Open |
| OB-OI-22 | Partner configuration (Amir): partners must be addable and editable; a partner can be switched to manual sending to the warehouse; add a button to send the 940 manually before the delay elapses; also a "send 940" action after a 997 rejection. | Proposed; not yet in requirements |
| OB-OI-23 | Email invoicing (Amir): an email address is required when the invoice method is email. | Proposed; not yet in requirements |
| OB-OI-24 | Unmapped items (Amir, plus one unnamed reviewer): send a notification with full details so the mapping can be fixed. | Proposed; aligns with FR-OB-15 |
| OB-OI-25 | Historical Shopify cut-off (unnamed reviewer; Amir): the "below 9000" rule exists to avoid fulfilling old orders that have no record in the system; old orders must not be fulfilled. Conflicts with the original proposal to drop the rule. | Decision needed |
| OB-OI-26 | Warehouse-modified order numbers (Amir): Radiant sometimes appends a character (for example "a") to the order number for orders fulfilled differently; 997 and 945 matching must handle this. | Proposed; design needed |
| OB-OI-27 | Folder locations (Amir): TrueCommerce and Radiant folders to be held in settings tables. | Proposed |
| OB-OI-28 | Addresses (Amir): country defaults to Canada when empty in the 940. | Proposed |
| OB-OI-29 | PO updates (Amir): update the order if the 940 has not been sent; if already sent, update and notify a user. Differs slightly from FR-OB-16 (review task instead of update). | Decision needed |
| OB-OI-30 | Retailer cancellations and returns after shipment (Amir) need handling. | Open — scope to define |
| OB-OI-31 | Scheduler (Amir): schedule must be user-configurable (currently every 5 minutes); no page refresh needed — run as a process with a lock; Zoho's traffic limits must not be carried over; robust error handling so the schedule is never interrupted. | Proposed; aligns with FR-OB-07 / FR-OB-50 |
| OB-OI-32 | Failed 940s (Amir): no automatic retry needed, only notify the user — conflicts with FR-OB-24 automatic retry. | Decision needed |
| OB-OI-33 | Notification management (Amir): a management page for emails and notifications. | Proposed |
| OB-OI-34 | Logging (Amir): comprehensive logs are required for root-cause analysis. | Proposed; aligns with FR-OB-53 |
| OB-OI-35 | Seed initialisation (Amir): the first-time seed data can be dropped; it was only for initial Zoho development. | Proposed |
| OB-OI-36 | Inventory re-evaluation (Amir): a re-runnable process to re-evaluate inventory from transfers and Radiant fulfilments, with adjustments for differences found; but full inventory recalculation may be postponed to the next version. Phase placement of FR-OB-40 to FR-OB-43 to be decided. | Decision needed |
| OB-OI-37 | Assignment of people to each permission (section 10.7). | Open |
