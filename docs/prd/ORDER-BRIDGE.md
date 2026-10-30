# Order Bridge — Product Requirements Document

- **Version:** 1.2
- **Date:** 2026-10-29
- **Module:** Sales (folders `sales` / `sales-api`, prefix `sales_`); Order Bridge is its first part (orders, fulfilment, invoicing, transfers). Renamed 2026-10-29 (D23); requirement IDs (FR-OB, AC-OB, OB-OI) and this file's name are unchanged.
- **Status:** Proposed — decisions applied, awaiting approval
- **Scope:** Rebuild the Zoho Creator "Order Bridge" order hub (Shopify and retailer EDI orders, Radiant 3PL EDI including transfers, retailer invoicing; shipment lots, expiry dates and transfer receipts recorded, with the inventory ledger in the next version) as a new UniOps module, and retire Zoho One.
- **Source:** Order Bridge PRD (English edition of the trilingual PRD issued 2026-10-09, based on the complete exported Zoho application script), the 39 team review comments collected on the PRD review board (2026-10-13 to 2026-10-15), the earlier SDM PRD (2026-10-01, reference only), and the product owner's decisions of 2026-10-20.

### What changed in this version

**1.2 (2026-10-20)** — completeness pass against the original 2026-10-09 PRD and the earlier SDM PRD, and the product owner's decisions D1–D16 (section 13.1).

- Appendix A records the interface formats (X12 envelope, 940/943/945/944/997 segments, 850 and 810 JSON, file names, Shopify mapping, connection endpoints) that FR-OB-20/21 require to be kept.
- 945 rules: line matching includes Item_Code; order matching handles 9-digit padding and Shopify numbers; one row per W12 lot; idempotency not keyed on tracking (LTL); 945s with no order are kept (FR-OB-25 to FR-OB-25c, FR-OB-28).
- File intake: store before parse, sha256 content dedup, 60-second rule, after-read policy, classification by ST01 (section 10.8, FR-OB-70 to FR-OB-76).
- Shopify API: client-credentials auth, no `customer` field, cursor rules (FR-OB-10). Manual-order lifecycle Draft → Active (FR-OB-19a).
- Smaller fixes: 997 AK5 reason codes, 2-hour ACK timeout default, one open task per problem, authentication failures not retried, stale runs closed, Operations Health checks and dashboard card definitions, print templates, enumerations, settings-key and partner-name migration maps, permission codes.
- Old orders: historical = below the cut-off or shipped/completed/cancelled in Zoho; in-flight orders continue (FR-OB-17).
- Product-owner decisions D1–D16 (2026-10-20) applied; see section 13.1. Inventory and Amazon FBA are out of this version; transfers (943/944) are in it (section 10.9, FR-OB-80 to FR-OB-84).
- Acceptance criteria AC-OB-01 to AC-OB-79 and replay targets added (section 10.10).
- 945 validation checks (notify only): over-shipment, extra or substituted item, expiry and minimum shelf life per partner, order state, mismatch with the 940, ship date; an extra item matching no 940 line also goes to review; a 945 before any 997 records ACK status Accepted by 945 (FR-OB-25d, AC-OB-56 to AC-OB-62).
- **Review fixes (2026-10-21):** delivery worklist requires shipment and requested date (FR-OB-31); Shopify lines to ship (FR-OB-11b); UniOps status model with Zoho mapping (section 9.1); notification gate before the channel switch (section 11); permissions updated; acceptance criteria AC-OB-70 to AC-OB-72; no duplicate Shopify fulfillment for orders migrated from Zoho (FR-OB-11); acceptance criteria listed in numeric order; decisions D17–D20 (On Hold principle, one 945 per order, no NC interface, 940 delay from the paid time).
- **Open items resolved (2026-10-22):** selling units (Shopify Case / 2-Pack / Each, FR-OB-02, FR-OB-14); one 944 per transfer, repeated W07 lines summed (FR-OB-83); 945 ship date from DTM, else G62 (FR-OB-25b); carriers (FR-OB-11); one-letter suffix rule (FR-OB-28); envelope values kept on evidence (Appendix A.2); real 943/944 samples in Appendix A.4/A.7, including the CF0093/CF0103 discrepancy; acceptance criteria AC-OB-75 to AC-OB-79; OB-OI-10, 14, 15, 16, 17, 18 and 26 closed.
- **Module renamed Sales (2026-10-29, D23):** Order Bridge becomes the first part of the Sales module; permission keys are `sales.*` (FR-OB-60 to FR-OB-66). Requirement IDs are unchanged.
- **Single user at go-live (2026-10-23, D21):** one user holds all permissions and receives all notifications and Action Required tasks (section 10.7, FR-OB-56); OB-OI-37 closed.
- **Document store (2026-10-23, D22):** raw documents are kept in Order Bridge's own document store rather than the shared file service (FR-OB-05, section 11 step 1).
- **Fresh build, not SDM:** Order Bridge is built as a new module; the earlier SDM code is not continued and its documents are reference material only. Completing our data from Radiant is planned for the next version (FR-OB-01, section 1.3).

**1.1 (2026-10-16)** — the 39 review-board comments are applied to the requirements. Comment numbers (#n) refer to the review board.

- **Partners and 940 sending:** partners are added and edited in the UI; the 940 delay and the Automatic/Manual sending mode are per-partner configuration set by authorised users, not fixed values in this document; "Send 940 now" is available before the delay elapses and after a 997 rejection (FR-OB-03a to FR-OB-03c; #1, #3, #10).
- **Historical Shopify orders:** the cut-off is kept — old orders must never be fulfilled (FR-OB-17; #6, #27).
- **Failed 940s:** no automatic retry; the user is notified and resends manually (FR-OB-24; #17).
- **PO updates:** applied before the 940 is sent; after it is sent, the order is updated and a user is notified (FR-OB-16; #11).
- **Warehouse-modified order numbers:** 945 matching tolerates a suffix that Radiant appends to the order number; 997s are matched by the control numbers they echo back, so no tolerance is needed there (FR-OB-28, FR-OB-22a; #7).
- **Scheduler:** configurable interval (default 5 minutes), background process with a lock, no Zoho-style throughput limits, errors never stop the schedule (FR-OB-07, FR-OB-50; #13 to #16, #24).
- **Business rules instead of Zoho boolean expressions:** payment-hold release, 940 eligibility, 997 matching, 945 line matching and dashboard counts are stated as explicit business rules, which removes the `A && B || C` precedence question; 940 eligibility must pass an acceptance test in the shadow run before any channel cutover (FR-OB-12, FR-OB-03d, FR-OB-03e, FR-OB-22a, FR-OB-25a, FR-OB-52; #26).
- **Configuration and master data:** SFTP folders (Radiant production `/prod/in`, `/prod/out`), item master, partners, DC routing and settings are editable in the UI with CSV import/export; the one-time Zoho migration uses the same CSV import, so there is no seed function and no configuration data in code; DC data is mandatory for Walmart only (FR-OB-02, FR-OB-03, FR-OB-09; #8, #20, #28, #32, #33).
- **Notifications and logs:** notification management page; unmapped items notify with full details; logs detailed enough for root-cause analysis (FR-OB-15, FR-OB-56, FR-OB-53; #4, #5, #18, #19).
- **Invoicing:** invoice email required for the email method, sent to the retailer with the internal employee in copy; invoices are editable; the delivery date is entered manually with reminders (replaced in 1.2 by delivery confirmation, D3) (FR-OB-31 to FR-OB-35; #2, #23, #29 to #31).
- **EDI:** country defaults to Canada; outbound EDI is validated before sending; known EDI defects may be corrected after confirming with Radiant (FR-OB-20, FR-OB-29, FR-OB-29a; #9, #22, #36).
- **Cancellations and returns after shipment** become a requirement (FR-OB-19; #12).
- **Inventory re-evaluation and ledger** are deferred to the next version (section 10.5; #21, #25).
- **Credentials:** no immediate password rotation; rotation happens at Zoho retirement; ftp-api.com is dropped (#38, #39).
- Open items answered by the comments are closed in section 13; the rest stay open.

**1.0 (2026-10-16)** — first entry of Order Bridge in the UniOps PRD set, carried over from the 2026-10-09 PRD and restated as numbered requirements (FR-OB-xx).

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
- Unified order records, line items, status tracking, holds and exception review; manual orders.
- EDI with Radiant: 940 shipping order, 945 shipment confirmation, 997 functional acknowledgement, and transfers (943 out, 997 and 944 in; section 10.9).
- Post-shipment write-back: Shopify fulfillment, tracking and archiving; invoices to retailers by each partner's method (810 JSON via TrueCommerce, email/PDF, or none).
- Shipment lots, expiry dates and pallet identifiers recorded from 945s (the basis for the next version's inventory ledger and for recalls).
- File register, scheduler run records, logs, operations health page and order dashboard.

### 1.2 Out of scope

- Accounting entries, receivables and cash collection. NC remains the financial source of truth; Order Bridge only produces invoice files (see FINANCE.md).
- Configuration of Shopify, TrueCommerce and Radiant themselves.
- Generic Zoho platform features (form builder, Zoho user system). UniOps uses its own framework (see PORTAL.md).
- Reuse of the earlier SDM implementation (`sdm`, `sdm-api`); see FR-OB-01.
- Inventory: ledger, balances, adjustments and the inventory dashboard move to the next version (section 10.5). No screen in this version shows an inventory figure.
- Amazon FBA.
- An interface to NC: Order Bridge does not push orders or invoices to NC in this version (OB-OI-09); NC remains the financial source of truth (FINANCE.md).

### 1.3 Next version

- Inventory ledger, balances, adjustments, re-evaluation and the inventory dashboard (section 10.5).
- **Completing our data from Radiant:** Amir will obtain data from Radiant to complete and reconcile Order Bridge's records (OB-OI-38).

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
- **Settings keys (Zoho → UniOps):** ERROR_NOTIFICATION_EMAIL and FINANCE_AR_EMAIL → notification settings (FR-OB-56); SHOPIFY_API_VERSION → Shopify connection; SHOPIFY_LAST_SYNC_DATE → Shopify cursor (handed over at cutover); DEFAULT_INVENTORY_WAREHOUSE_CODE → warehouse configuration; RADIANT_ / TRUECOMMERCE_DELETE_AFTER_DOWNLOAD → after-read policy (FR-OB-74). Not migrated: WORKDRIVE_* (the Order Bridge document store replaces WorkDrive), RETRY_MAX_COUNT / RETRY_INTERVAL_MINUTES (never read), INVENTORY_RECALC_LOCK_* (replaced by the scheduler lock).
- **Connection endpoints:** Radiant SFTP `commhub.wheelsgroup.com` (production `/prod/in`, `/prod/out`); TrueCommerce SFTP `sftp.truecommerce.com` (pull from `Export`, send to `Import`); Shopify Admin API.
- **DC routing:** 457 rows, all Walmart, covering 57 Walmart DCs; no other retailer has routing data.

The table shows the Zoho seed. In UniOps the 940 delay is per-partner configuration set at go-live (FR-OB-03a); it is not fixed by this document.

### 3.5 Data-model problems to fix in the rebuild

1. The four order status lines overlap; one must be the source and the rest derived.
2. Most picklists accept values outside the list.
3. The file register's partner field is a static list that does not match the partner table (JeanCoutu vs Jean Coutu; no Overwaitea or Manual; code also writes "Radiant"). Migration maps these names to partner records; "Radiant" maps to the system field, not a partner.
4. Some field names contradict their UI labels (on-hand shown as "Preparing for Fulfillment"; delivered quantity shown as "Quantity In").
5. Adjustments have no warehouse; line numbers are text (10 sorts before 2); quantities mix integers and decimals.
6. Re-running the seed overwrites configuration edited in the UI; Radiant's seeded paths point to test directories. (The rebuild has no seed-initialisation function; see FR-OB-09.)
7. Two retry settings are never read; one setting read by code is never seeded.
8. CF0110's UPC has 11 digits; every item but CF0093 shares the same gross weight (likely a placeholder).
9. **Security:** SFTP passwords and the Shopify token are stored in plain text in the seed function, the connection table and the export file. Risk accepted until cutover; credentials are rotated at Zoho retirement (OB-OI-20, section 11).

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
| Transfer status | Draft → Ready To Send → 943 Generated → Waiting for 997 → Waiting for 944 → Received / Partially Received / Discrepancy; Failed on 997 rejection; Cancelled (UniOps, before the 943 is sent, or after it was rejected (Failed); FR-OB-80) |
| File processing / ACK | Processing: Pending → Processing → Processed / Failed / Skipped (inbound); Ready To Send → Sending → Sent / Failed (outbound). File status: Ready, Sent, Processed, Accepted, Rejected, Failed, Cancelled. ACK: Not Required / Waiting / Accepted / Accepted by 945 / Rejected / Timeout |
| File document type | 850_JSON, 850_EDI, 940_EDI, 943_EDI, 944_EDI, 945_EDI, 997_EDI, 810_JSON, INVOICE_EMAIL, SHOPIFY_ORDER_JSON, OTHER |
| File system / direction | Radiant, Shopify, TrueCommerce, Email; Inbound / Outbound |
| Order line status | Zoho: Active, Deleted, Shipped, Partially Shipped, Cancelled, Backordered, Error. UniOps (one 945 per order, D18): Active, Shipped, Short Shipped, Cancelled, Error |
| Order unit of measure | EA, Case, Can |

Values defined but never written (ACK Timeout, 943 Sent, 997 Accepted, transfer Cancelled) must be implemented or removed: UniOps implements ACK Timeout (FR-OB-23) and transfer Cancelled (FR-OB-80) and drops 943 Sent and 997 Accepted, which Waiting for 997 and Waiting for 944 already cover. The UniOps status model, and how each Zoho value maps to it, is in section 9.1.

### 9.1 UniOps status model

Four status lines are the source; each value has a closed list (FR-OB-08). Order state is **derived**, never stored independently, and Zoho's eight-stage Order status is not carried over (the order screen shows the source lines instead). Historical (FR-OB-17) is a flag on the order, not a status.

**Fulfillment status** (per order)

| Value | Meaning | Next |
| --- | --- | --- |
| Draft | Manual order not yet submitted (FR-OB-19a) | Pending (submit), Cancelled |
| Review | Needs a decision: unmatched item, zero quantity, unknown partner, case-pack mismatch, Walmart order without DC (FR-OB-02, FR-OB-03, FR-OB-15) | Pending (resolved), Cancelled |
| Payment Hold | PREPAID order not yet paid (FR-OB-12) | Pending (paid), Cancelled |
| Pending | Waiting for the 940: delay running, or Manual-mode partner waiting for Send 940 now (FR-OB-03d) | 940 Generated, Archive Suppressed, Cancelled |
| Archive Suppressed | Archived in Shopify before any 940 (FR-OB-03d) | Pending (un-archived), Cancelled |
| 940 Generated | 940 built and validated (FR-OB-29a) | 940 Sent, 940 Failed |
| 940 Sent | Sent; waiting for the 997 | 940 Accepted, 940 Rejected, 940 Timeout, Shipped (945 first: ACK Accepted by 945) |
| 940 Accepted | 997 accepted; waiting for the 945 | Shipped |
| 940 Rejected / 940 Timeout / 940 Failed | Needs a person (FR-OB-22a, FR-OB-23, FR-OB-24) | 940 Generated (Send 940 now), Cancelled |
| Shipped | Fulfilment closed by the order's one 945, including a short shipment with its shortfall task (FR-OB-25) | On Hold (a second, different 945 arrives) |
| On Hold | Cancelled at source after the 940 was sent, waiting for the outcome with Radiant (FR-OB-18) | Cancelled, Shipped, or the status it held before |
| Cancelled | Cancelled before shipment | — |

**Invoice status** (per order)

| Value | Meaning | Next |
| --- | --- | --- |
| Not Required | Invoice method None (Shopify, Manual) | — |
| Pending | Invoice method set; not shipped yet | Awaiting Requested Date, Waiting for Delivery Confirmation |
| Awaiting Requested Date | Shipped; requested delivery date not reached | Waiting for Delivery Confirmation |
| Waiting for Delivery Confirmation | Shipped and requested date reached; on the worklist (FR-OB-31) | Ready |
| Ready | Delivery and delivered quantity confirmed | Queued (810 JSON), Sent (email) |
| Queued | 810 JSON generated, waiting to be sent | Sent, Failed |
| Sent | Invoice delivered; may be edited and resent (FR-OB-35) | Sent |
| Failed | Generation or sending failed | Queued (resend) |
| Blocked | Lot exception (FR-OB-25), or cancellation / return after shipment (FR-OB-19) | the status it held before, once cleared |

**Entries not shown in the Next columns (product owner, 2026-10-27):** fulfillment On Hold is entered from 940 Sent, 940 Accepted, 940 Timeout and Shipped (FR-OB-18, FR-OB-25; 940 Rejected uses its Cancelled exit); invoice Blocked is entered from Pending, Awaiting Requested Date, Waiting for Delivery Confirmation, Ready, Queued, Failed and Sent (FR-OB-25, FR-OB-19); both return only to the status held before (or, for On Hold, to Cancelled or Shipped); and 940 Generated → Cancelled is allowed, because the 940 is not sent yet (FR-OB-18) — the generated file is cancelled with it. A timeout means a person should look, not that no answer can come: 940 Timeout → 940 Accepted / 940 Rejected (late 997) / Shipped (945), and ACK Timeout → Accepted / Rejected / Accepted by 945; the timeout task is closed when the late answer arrives. A failed transfer is resent or cancelled: transfer Failed → 943 Generated (FR-OB-81, FR-OB-24) or → Cancelled (FR-OB-80, user decision 2026-10-27); an ACK timeout leaves the transfer in Waiting for 997 and sets only its ACK to Timeout. **Source updates before the 940 (product owner, 2026-10-29):** an update that makes the order invalid sends it to Review from Pending or Payment Hold (FR-OB-15, FR-OB-16); a resolved review of a PREPAID order that is still unpaid goes to Payment Hold, otherwise to Pending (FR-OB-12).

**Transfer status:** Draft → Ready To Send → 943 Generated → Waiting for 997 → Waiting for 944 → Received / Partially Received / Discrepancy; Failed on 997 rejection; Cancelled before the 943 is sent, or after it was rejected (Failed); On Hold when a second, different 944 arrives (FR-OB-80 to FR-OB-84).

**File and ACK status:** inbound processing Pending → Processing → Processed / Failed / Skipped, plus Duplicate (FR-OB-71); outbound Ready To Send → Sending → Sent / Failed / Suppressed, plus Replaced for a 940 superseded by a confirmed replacement (FR-OB-03c) and Cancelled. Suppressed means the send or write was refused because its environment switch is off (dev and test never reach partners; product owner, 2026-10-28): it is never sent automatically, not even when the switch is turned on — a person reviews it and resends (Suppressed → Sending) or cancels it. ACK: Not Required, Waiting, Accepted, Accepted by 945, Rejected, Timeout.

**On Hold principle (D17):** whenever something is wrong and a person needs to check it, the order's state is On Hold. **Order state (derived):** Draft when fulfillment is Draft; Cancelled when fulfillment is Cancelled; On Hold when fulfillment is Review, Payment Hold, Archive Suppressed, 940 Rejected, 940 Timeout, 940 Failed or On Hold, or invoice is Blocked or Failed; Completed when fulfillment is Shipped and invoice is Sent or Not Required; otherwise Active.

**Zoho → UniOps map (migration)**

| Zoho line | Zoho value | UniOps value |
| --- | --- | --- |
| Fulfillment | Pending | Pending |
| Fulfillment | Payment Required | Payment Hold |
| Fulfillment | 940 Generated | 940 Generated |
| Fulfillment | 940 Sent - Waiting for ACK | 940 Sent |
| Fulfillment | Waiting for 945 | 940 Accepted; for Shopify orders the open fulfillment Zoho created is stored so the 945 adds tracking to it (FR-OB-11) |
| Fulfillment | Confirmed, Fulfilled | Shipped |
| Fulfillment | Rejected / Timeout / Failed | 940 Rejected / 940 Timeout / 940 Failed |
| Fulfillment | Review Required | Review |
| Fulfillment | On Hold | On Hold |
| Fulfillment | Cancelled | Cancelled |
| Invoice | Not Required, Ready, Queued, Sent, Failed | same value |
| Invoice | Pending | Pending, or Awaiting Requested Date / Waiting for Delivery Confirmation if already shipped (by the FR-OB-31 rule) |
| Invoice | Waiting for Delivery Date | Awaiting Requested Date or Waiting for Delivery Confirmation, by the FR-OB-31 rule |
| Order state / Order status | any | not migrated; derived. Completed or Cancelled orders are also flagged historical (FR-OB-17) |
| Transfer | any | same value (943 Sent and 997 Accepted were never written) |
| File / ACK | any | same value |

## 10. Requirements for the UniOps rebuild

Business behaviour follows sections 4–9 1:1 except where a requirement below fixes a defect or applies a review decision. Cutover is channel by channel, not all at once. "(#n)" cites the review-board comment a requirement comes from.

### 10.1 Architecture, configuration and scheduling

| ID | Requirement |
| --- | --- |
| FR-OB-01 | Order Bridge is built as a **new standalone UniOps module** — named **Sales** (D23), of which Order Bridge is the first part — built fresh, with its own back-end service and database schema and its own front end, reachable from the Portal (PORTAL.md). The earlier SDM implementation (`sdm`, `sdm-api`) is **not continued or reused**; its documents may be cited as reference material only. |
| FR-OB-02 | Order Bridge keeps an **Item Master table** that authorised users can add to and edit in the UI (SKU, UPC, GTIN, buyer part numbers per partner, case pack, gross weight per can, unit-of-measure conversions) (#32). A buyer part number matches only lines from the partner it belongs to. Every item has an EA conversion of exactly 1. **Selling units:** retailers order in cases; Shopify sells **Case, 2-Pack and Each**. Each unit is converted to EA through the item's conversions in the Item Master (today Case = 6 EA, 2-Pack = 2 EA, Each = 1 EA). The Shopify variant-title rule (Appendix A.11) is only a migration and matching hint; a line whose unit has no conversion goes to review (FR-OB-15). **Item matching (product owner, 2026-10-30):** a Shopify line matches by SKU, then by its variant's barcode (read from Shopify, as the order line carries none) against the item's GTIN, then its UPC — never by item code; a retailer 850 line by GTIN, UPC, then that partner's buyer part. Barcodes compare in GTIN-14 form (digits only, left-padded with zeros), so UPC-A, EAN-13 and GTIN-14 forms of one code match, and a lost leading zero does not matter; a barcode on two items matches neither. Anything that does not match, or whose barcode cannot be read, goes to review — never a guess. The **Item Master is authoritative for case pack**: if an 850 line's `ofInnerPacks` differs from the Item Master, the order goes to review and a user is notified; the 850 value is never used silently, because Radiant ships EA exactly as told. Alignment with UniOps master data (EPMS.md, MRP.md) is a later step. |
| FR-OB-03 | Trading partners, DC routing and warehouses are configuration tables that authorised users can add to and edit in the UI (#3). DC routing references the partner directly (no fuzzy name matching). Each partner has a **settlement mode**: PREPAID (Shopify — payment must be received before a 940) or INVOICE (retailers — billed after delivery, no payment check). Each partner has an optional **minimum remaining shelf life** (days at ship date; empty = no check) used by FR-OB-25d. Each partner has a "DC routing required" flag: **on for Walmart** — a Walmart order with no DC match goes to review and notifies a user; off for others — a missing DC is ignored and the LO reference is left empty (#33). |
| FR-OB-03a | Each partner has a **940 delay in minutes**, set by authorised users in the partner configuration (FR-OB-03) together with the sending mode (FR-OB-03b). This document does not fix the values: they are configured at go-live. Today's intent is Shopify 3 hours; retailer delays are set per partner (review comment #1 suggests 1 hour; the September decision used 0 — whichever is configured applies) (#1). |
| FR-OB-03b | Each partner has a 940 sending mode: **Automatic** (the 940 is sent when the delay elapses) or **Manual** (the 940 is only sent when a user triggers it) (#3). |
| FR-OB-03c | A **Send 940 now** action on an order sends the 940 immediately, before the delay elapses or for a manual-mode partner. The same action is available after a 997 rejection or a send failure to resend (#3, #10, #17). It is permission-controlled (FR-OB-61) and recorded in the audit log. A **live 940** is one that has been sent and is not 997-rejected, failed, timed out or cancelled. Send 940 now is refused while a live 940 exists, except as a **replacement resend** after a source change (FR-OB-16): the user must confirm explicitly, the previous 940 is marked Replaced, and the new 940 gets a new control number. Send 940 now and replacement resend are **refused for historical orders** (FR-OB-17). |
| FR-OB-03d | **940 eligibility (automatic sending).** An order is queued for a 940 only when **all** of these hold (no OR): order state Active; fulfillment status Pending; **payment satisfied** — for a PREPAID partner the Shopify financial status is `paid` or `partially_refunded` (`refunded`, `authorized`, `partially_paid`, `pending` and `voided` do not qualify), for an INVOICE partner there is no payment check; not fulfilled; **not archived in Shopify during the delay window** (an order archived before any 940 is suppressed and the suppression is recorded on the order; if its payment is satisfied, **one Action Required** task is also raised naming the order, the customer order number, the amount paid and the archive time, so someone confirms the archive was intended or un-archives the order — refunded or voided orders raise no task. An order **un-archived** in Shopify becomes eligible again under these rules with no extra step (the delay has usually elapsed, so it is queued on the next cycle), and the task is **closed automatically** when the order is un-archived, refunded or cancelled, because the condition is gone; archiving after fulfilment is ignored); **not historical** (FR-OB-17); partner sending mode Automatic; the partner's 940 delay has elapsed, measured **from the paid time** (D20): for a PREPAID partner, from when payment became satisfied — at receipt if already paid, otherwise when it is released from Payment Hold — and for an INVOICE partner, from when the order was first received. Later source updates do not restart it. Orders of Manual-mode partners get a 940 only through Send 940 now (FR-OB-03c) (#26). This gate is the second of two independent guards against fulfilling old orders: legacy orders already shipped in Zoho (7,475 of 7,719 imported Shopify orders) fail "not fulfilled" and never qualify even without the cut-off. |
| FR-OB-03e | **Acceptance test for 940 eligibility.** After implementation, the FR-OB-03d rule is tested with cases covering each condition, and again in the shadow run (section 11 step 2) against Zoho's 940 decisions. A **full-history dry run must queue zero historical orders and queue the in-flight unsent orders exactly as expected**, and Send 940 now must be refused for a historical order. No channel is cut over until all of these pass. |
| FR-OB-04 | SFTP and Shopify credentials are held as server secrets, never in the database or the UI. SFTP is connected directly; the ftp-api.com relay, which existed only because of a Zoho limitation, is dropped (#39). |
| FR-OB-05 | Raw inbound and outbound files are archived in the **Order Bridge document store** (raw bytes held by Order Bridge itself, D22), replacing WorkDrive. Original bytes are kept; nothing is ever deleted (needed for recalls). See section 10.8 for intake rules. |
| FR-OB-06 | Wherever this document says "notify a user", the notification follows the notification settings (FR-OB-56): email and/or an owned task in the UniOps Task Inbox. Exceptions that need someone to act always create a Task Inbox task. **One open task per problem**: a problem that recurs every cycle updates its open task instead of raising a new one (a source failing every 5 minutes must not raise 288 tasks a day). Each task carries a copy of the identifying context (order number, PO, partner, file, totals) taken when it is raised, so it can be acted on even when no order record exists. |
| FR-OB-07 | Processing runs as a **background scheduled process** inside the module: the interval is configurable by authorised users in the UI (default **every 5 minutes**, matching today's external call) and can be paused; a lock prevents overlapping runs; every cycle writes a run record. A run left RUNNING by a killed process is closed as FAILED by the next cycle. Opening or refreshing a page never triggers processing (#13, #16, #24). |
| FR-OB-08 | Unique indexes and closed enumerations replace "look up, then insert" and open picklists. |
| FR-OB-09 | No configuration data lives in code: there is no seed function and no data files in the repository (#20). Partners, item master, DC routing (457 Walmart rows today), warehouses and settings — including SFTP folder paths (inbound, outbound, archive) per connection (#8), with Radiant production paths **`/prod/in`** and **`/prod/out`** (#28) — are editable in the UI and each table has **CSV import and export** in the UI. The one-time Zoho migration of these tables uses the same CSV import (Zoho export → upload). Credentials are never held in these tables (FR-OB-04). |

### 10.2 Inbound orders

| ID | Requirement |
| --- | --- |
| FR-OB-10 | Pull Shopify orders incrementally through the Admin API (version configurable; `2026-04` today) with pagination and correct time-zone handling. Authentication uses the **client-credentials grant** (permanent `shpat_` tokens are no longer issued since 2026-01-01); the 24-hour token is cached and renewed before expiry. The order query **does not request `customer`** (the app lacks `read_customers`, and Shopify then fails the whole page with ACCESS_DENIED); email and phone are read from the order itself. The `updated_at` cursor is inclusive and never nudged forward (the boundary order returns and is recognised as a duplicate), advances only after the batch is committed, never moves backwards, and is the maximum across the batch rather than the last row seen. No order is skipped when many share an update time. |
| FR-OB-11 | Read Shopify shipping, tax and discounts; use the actual carrier (Purolator for Shopify orders; retailer orders ship LTL with SCAC WIFS, Appendix A.3); support multiple fulfillment orders. The **Shopify fulfillment is created when the 945 arrives**, not at 997 acceptance, with the tracking number where present (the customer is notified); nothing is written to Shopify at 997 acceptance. **Orders migrated from Zoho:** Zoho created the fulfillment (without tracking) at 997 acceptance, so a Shopify order that already has an open fulfillment created by Zoho gets the 945 tracking **added to that fulfillment** (customer notified) instead of a second fulfillment. The existing fulfillment id is stored at migration, and the order's fulfillments are also read from Shopify when the 945 arrives. |
| FR-OB-11a | **Archive in Shopify.** When Order Bridge completes the fulfilment of a Shopify order, it archives (closes) the order in Shopify, as Zoho did; otherwise every shipped order looks like open work in the store. |
| FR-OB-11b | **Shopify lines to ship.** The 940 carries only the current quantity of shippable product lines: the ordered quantity less refunded and removed quantity. Gift cards, lines that require no shipping, digital lines and fully refunded lines are excluded. A line refunded or removed after the 940 has been sent is handled as a source change (FR-OB-16). |
| FR-OB-12 | **Payment hold release (Shopify).** An order is released from hold automatically only when it is On Hold **and** its hold reason is Payment Required **and** its Shopify financial status satisfies payment as defined in FR-OB-03d (`paid` or `partially_refunded`). Orders that are `authorized` or `partially_paid` stay on hold. The same definition applies at creation and at release. A released order then waits the partner's full 940 delay from the payment time (FR-OB-03d, D20). Principle: the rebuild does not translate Zoho's boolean expressions (written as `A && B \|\| C`, with ambiguous precedence); each rule is stated in business terms — this one, FR-OB-03d, FR-OB-22a, FR-OB-25a and FR-OB-52 (#26). |
| FR-OB-13 | Pull TrueCommerce 850 JSON files directly over SFTP; process every PO in a file, not only the first. |
| FR-OB-14 | Convert quantities using the actual ordered unit — cases on retailer 850s; Case, 2-Pack or Each on Shopify — and the Item Master conversion (FR-OB-02). All internal quantities and **all messages exchanged with Radiant are in EA** (#35); conversion happens only at the retailer and Shopify boundaries. **Shopify unit (product owner, 2026-10-30):** the variant title decides — the item's pack unit it names, otherwise EA, with the pack-looking-title safety net of Appendix A.11. |
| FR-OB-15 | Unmatched items, zero quantities, unknown partners, a unit with no conversion, and a conversion that does not give a whole number of EA (it is never rounded; product owner, 2026-10-28): the order **is created and goes to review** (no 940 until resolved) and a notification is sent with the full details needed to fix the mapping — order, partner, source line, the item identifiers received (GTIN/UPC/SKU/buyer part, variant title, unit) and the reason (#4, #5). Nothing is created silently. |
| FR-OB-16 | Source updates (Shopify or 850 resend): **before the 940 is sent**, the order and its lines are updated. **After the 940 is sent**, the order is updated (#11), but the values the 940 carried (item, quantity, unit, warehouse, ship-to) stay locked on the 940 record; an **Action Required** task is raised and a user is notified with a before/after comparison of the 940 values and the new order values, so they can act with the warehouse (for example send a replacement 940 under FR-OB-03c, after agreeing it with Radiant). The 940 is not resent automatically. Lines that already have 945 shipment data are never rebuilt. |
| FR-OB-17 | **Old orders are never fulfilled** (#6, #27). An order is **historical** when it is below the Shopify order-number cut-off (configurable, today 9000), or when it was shipped, completed or cancelled in Zoho before cutover. Two independent guards apply, so that either one alone prevents a 940: (1) the cut-off; (2) the eligibility gate of FR-OB-03d. Historical orders may be imported read-only for reporting, but are never eligible for an automatic 940, Send 940 now or a replacement resend (FR-OB-03c). **In-flight orders** migrated at cutover — no 940 yet, or 940 sent and awaiting the 997 or 945 — are **not** historical: they migrate with their status and continue their flow under FR-OB-03d. |
| FR-OB-18 | **Source cancellation** (Shopify or 850). Before the 940 is sent, the order is cancelled silently: no email and no task; it simply stops qualifying. After the 940 is sent, or after anything has shipped, the order goes on hold, an **Action Required** task is raised, and **one email** is sent to Radiant and the internal owner (recipients per FR-OB-56) stating the shipped quantity, so that "nothing shipped, please do not ship" and "N units already shipped" read differently. |
| FR-OB-19 | Retailer orders cancelled or returned **after shipment** can be recorded on the order (cancelled after shipment / returned, with quantities and reason), which stops or flags invoicing and notifies a user (#12). Credit-note and inventory handling for returns: see OB-OI-30. |
| FR-OB-19a | **Manual orders.** An authorised user (FR-OB-61) can create an order by hand (sample, phone order, staff purchase) for the Manual partner. It is created as **Draft**, which is never eligible for a 940; the entry screen previews each line's conversion to EA before saving. On **Submit** it becomes Active and from then on follows the same flows as any other order (eligibility FR-OB-03d, 940, 997, 945, invoicing). Creation, edits and submission are audited (FR-OB-53). |

### 10.3 Radiant EDI

| ID | Requirement |
| --- | --- |
| FR-OB-20 | Reproduce the 940 and 943 envelopes, segment structure and field values as specified in **Appendix A** (including label, DC, order-type and department references and carrier codes). Before cutover, compare old and new files for the same order segment by segment, except control numbers (FR-OB-22) and corrections confirmed with Radiant (AC-OB-25). Known EDI defects may be corrected, but only after the correct value is confirmed with Radiant (#36); the ISA 00401 / GS 006030 pairing and GS01 = OW on the 943 are kept, because Radiant uses them itself (OB-OI-16). Where a requirement in this section changes Zoho behaviour (control numbers FR-OB-22, country FR-OB-29), the requirement wins over Appendix A. |
| FR-OB-21 | Keep the 810 JSON structure, the 850 JSON keys, the 945/944/997 parsing positions and the file-naming rules of **Appendix A** unchanged. Each 940 file carries exactly one order (#34). |
| FR-OB-22 | Control numbers come from **one shared sequence** for every outbound X12 document (940 and 943), as in Zoho: allocated transactionally, unique, never reused — a resend takes a new number — and starting above the highest number used by Zoho. The same 9-digit zero-padded value is used for ISA13, GS06 and ST02. Leading zeros are normalised when matching 997s. Radiant places no limit on the range (#37). |
| FR-OB-22a | **997 matching.** A 997 is matched to our outbound file by the control numbers it echoes back — AK1 group control number (= our GS06) **and** AK2 transaction set control number (= our ST02), leading zeros normalised — plus the same partner **and** the same functional group / document type (940 or 943). Order numbers play no part in 997 matching. AK9 (group) and AK5 (transaction) are both read; A or E is accepted, R or anything else is rejected, and a rejection records its AK5 reason code (seen in practice: 4 "transaction set not supported", 5 "one or more segments in error") on the task. A rejected 940 is not resent automatically. No match or more than one match → the 997 goes to review and a user is notified (#26). |
| FR-OB-23 | Mark an outbound file Timeout when no 997 arrives within a configured time (default **2 hours**), and notify a user per FR-OB-56 (including a Task Inbox task) naming the order, partner and send time. A timed-out 940 is not resent automatically. A 945 that arrives before any accepted 997 stops the clock and records Accepted by 945 (FR-OB-25d). |
| FR-OB-24 | A failed 940 (generation or sending) is **not retried automatically**; the user is notified with the error details and resends with **Send 940 now** (FR-OB-03c) (#17). Other outbound files follow the same rule and are resent from the file register (FR-OB-62). |
| FR-OB-25 | **945 idempotency and short shipment.** Applying the same 945 twice changes nothing: shipment rows are keyed on the 945 document identity (content hash, FR-OB-71) plus pallet (LX / SSCC) and W12 position — **not** on tracking number, because LTL pallets carry no tracking number (150 of 1,084 archived W12 lines). A 945 that ships less than ordered **closes the order's fulfilment** with the shipped quantity and raises an **Action Required** task naming the order, the item and the quantity not shipped; no second 940 is raised automatically. Radiant sends **exactly one 945 per 940 order** (D18, #34), so the first 945 always closes the fulfilment. A **second, different 945** for an order already Shipped is stored but **not applied** to quantities (no double counting); the order goes On Hold with an Action Required task showing both 945s, and the FR-OB-25d notification is sent. A byte-identical repeat is a duplicate (FR-OB-71) and raises nothing. Lot exceptions block invoicing. |
| FR-OB-25a | **945 line matching.** A 945 line matches an order line only when the line belongs to the same order **and** (the LX line number matches **or** the W12 item identifier matches the line's Item_Code, buyer part number, UPC or GTIN). Item_Code is included because it is what we send in `W01 … VN` (Appendix A.3). A 945 line never matches a line of another order; an unmatched line goes to review and a user is notified (#26). |
| FR-OB-25b | **945 lots.** Every W12 under every LX is read — not only the first (a parser that keeps only the first W12 per LX lost 46 lot codes across the archive in the earlier SDM analysis). Each W12 is stored as one shipment row with its own lot, expiry, pallet SSCC-18 (stored as a string, keeping leading zeros), quantity and weight. The ordered quantity repeats on every W12 of the same item and is counted once; only shipped quantities are summed into the order line. Tracking (N9\*2I), carrier and mode (W27, N9\*MB carrier reference, N9\*BM bill of lading) are kept on the shipment. The **ship date** is read from DTM; when DTM has no value, G62 is used (OB-OI-15). |
| FR-OB-25c | **945 with no matching order.** A 945 that matches no order is stored with its pallets and lots (no order-line reference) and raises a task that offers to create an order from it; it is never dropped (these are reships and customer-service sends). |
| FR-OB-25d | **945 validation checks.** The 945 is applied as received; each check below **only notifies** (event "945 validation", FR-OB-56) with the order, file, line and the values found vs expected. A check never blocks processing or invoicing and never puts the order in review, with **one explicit exception**: an extra or substituted item that matches no 940 line also goes to review under FR-OB-25a, because it cannot be invoiced without a decision; the notification is sent as well. Other existing rules on the same cases (FR-OB-25 short shipment, FR-OB-25c no order, lot exceptions blocking invoicing) apply unchanged. (1) **Over-shipment:** a line's shipped quantity exceeds the quantity on the 940. (2) **Extra or substituted item:** a W12 item that was not on the 940. (3) **Expiry:** a lot is already expired at the ship date, or its remaining shelf life at the ship date is below the partner's minimum (FR-OB-03; no check when empty). (4) **Order state:** the 945 arrives for an order that is cancelled, has no accepted 997 yet, or is already Shipped. For an already Shipped order this check is narrowed by FR-OB-25: the second 945 is not applied and the order goes On Hold (a byte-identical repeat raises nothing). When no 997 has been accepted, the 940's ACK status is recorded as **Accepted by 945** — a value distinct from a 997 acceptance, visible in reports — and the ACK-timeout clock (FR-OB-23) stops for that file. (5) **Mismatch with the 940:** a line's quantity differs from the 940 other than a short shipment (FR-OB-25), or the ship-to or partner differs from the 940. (6) **Ship date:** in the future, or before the 940 was sent. |
| FR-OB-26 | Read inbound X12 separators from the ISA segment instead of hard-coding them. |
| FR-OB-27 | Move hard-coded values (company and warehouse addresses, SCAC, label codes, fallback weights, invoice letterhead) into configuration. |
| FR-OB-28 | **945 order matching.** The order key is read from W06-02, or W06-06 when W06-02 is empty, and looked up by Shopify order id, then internal order number, then source order number. Comparison normalises leading zeros (retailer 940s send the internal number zero-padded to 9 digits) and covers Shopify orders, whose W05 carries the Shopify order number (Appendix A.3). It also tolerates numbers that Radiant has modified by appending **one letter**, usually `a` or `b` (for example `1000123a` for an order fulfilled differently): exactly one trailing letter `[A-Za-z]` is stripped and the base number is matched; the warehouse's number is stored as received and shown on the order. If the base number matches no order or more than one, the file goes to review and a user is notified (#7). 997s need no such tolerance because they are matched by control number (FR-OB-22a). |
| FR-OB-29 | Ship-to and bill-to country in the 940 is sent as received; when it is empty, it defaults to Canada (#9). |
| FR-OB-29a | Validate every outbound EDI file before sending (mandatory segments and fields, quantities in EA, addresses, DC reference where required, control numbers). A file that fails validation is not sent and a user is notified. Text is written in plain ASCII: accented letters are transliterated (é → e) and the change is noted on the document; a character with no ASCII equivalent (for example a Chinese name) fails validation, and the notification names the field and the character so the person can edit it — a "?" is never written (product owner, 2026-10-28). Rationale: wrong EDI data or format can cause large losses (#22). |

### 10.4 Invoicing

| ID | Requirement |
| --- | --- |
| FR-OB-30 | Invoice by each partner's configured invoice method (810 JSON, email/PDF, none), unchanged from today. Invoice lines are priced per case (UOM CA) at the unit price received on the 850, with no price list; payment terms are partner configuration. |
| FR-OB-31 | **Delivery confirmation gates the invoice** (#23, #29). An order joins the delivery-confirmation worklist only when **both** hold: it has **shipped** (its 945 has been applied) **and** its **requested delivery date** has been reached (Walmart never sends RequestedDeliveryDate; its DoNotDeliverAfter is used instead). A person confirms delivery and records the **delivered quantity**, which may be less than shipped; the confirmed date becomes the invoice date and the invoice states the confirmed quantity. **Nothing is invoiced without this confirmation.** Unconfirmed orders stay on the worklist and age; there is no automatic escalation (the reminder notification in FR-OB-56 may stay). The worklist supports confirming orders in bulk. |
| FR-OB-32 | Keep the finance document email (PO, invoice, packing list) to finance AR; all user-supplied text is escaped in emails. |
| FR-OB-33 | When a partner's invoice method is Email, an invoice email address is **mandatory** in the partner configuration (#2). |
| FR-OB-34 | Where a retailer has an invoice email configured, the invoice is sent **directly to that address with the internal employee in copy** (replacing today's manual forwarding, e.g. Jean Coutu) (#30). |
| FR-OB-35 | Authorised users can **edit an invoice** (quantities, unit prices, dates) before it is sent, and correct and resend it afterwards; every edit is audited (#31). |

### 10.5 Inventory (deferred to the next version)

Inventory recalculation and the ledger are **postponed to the next version** (#25). In this version, lots, expiry dates and tracking from 945s and receipt lines from 944s continue to be recorded so the ledger can be built from them later. The requirements below define the next version.

| ID | Requirement |
| --- | --- |
| FR-OB-40 | Replace balance snapshots with a movement ledger: every receipt, shipment, reservation and adjustment writes a signed movement; balances are summed from the ledger. |
| FR-OB-41 | Redefine quantities as on hand, allocated, in transit and available; support opening stock. |
| FR-OB-42 | Manual adjustments carry a warehouse and follow Draft → Posted → Reversed, with reversal creating an offsetting movement. |
| FR-OB-43 | A re-runnable **inventory re-evaluation** process rebuilds inventory from transfers and Radiant fulfilments, so inventory can be re-checked at any time for auditing; differences against real counts are corrected through adjustments (FR-OB-42) (#21). |

### 10.6 Reliability, visibility and audit

| ID | Requirement |
| --- | --- |
| FR-OB-50 | Error handling never interrupts the schedule: one failing order, file or step is recorded, notified and skipped, and the cycle continues with the rest (#15). The Zoho throughput limits are not carried over — no 10-unit budget, no "2 inbound files per cycle" limit, no "20 Shopify orders per run" limit, and no "inbound backlog stops outbound" rule (#14). An **authentication failure** (SFTP or Shopify) is reported separately and that connection is not retried on the following cycles until a user clears it, so repeated refused logins do not lock the account. |
| FR-OB-51 | Every failure (850 / Shopify parse, SFTP fetch, send, 940 generation, validation) notifies a user and appears on the health page. |
| FR-OB-52 | Provide an Operations Health page equivalent to today's diagnostic, plus an Order dashboard. There is no inventory dashboard in this version (section 1.2). Every count is defined from the same status definitions and business rules as FR-OB-03d, FR-OB-12, FR-OB-22a and FR-OB-25a, never from a condition of its own (#26). |
| FR-OB-52a | **Operations Health checks.** Result is PASSED, WARNING or FAILED. **FAILED** when: a required setting is missing; a connection (Radiant, TrueCommerce, Shopify) is inactive or failing authentication; more than one cycle is running or a cycle is stuck RUNNING; the scheduler has been paused longer than a configured duration. **WARNING** for: the scheduler paused deliberately by a user (within that duration); failed inbound or outbound files; rejected or timed-out 997s; unmatched 945 or 944 lines; 945s with no order; transfer discrepancies; open tasks older than a configured age. Count cards: critical issues, warnings, failed inbound, failed outbound, waiting ACK, rejected ACK, ACK timeout, 944 exceptions, 945 exceptions, transfer discrepancies. Each check links to the list of records behind it. |
| FR-OB-52b | **Order dashboard cards**, computed from the UniOps status model (section 9.1): Active; On Hold; Completed; Cancelled (derived order state); Failed Files (file processing Failed, inbound and outbound); Open Retailer / Shopify / Manual (by source, order state not Completed or Cancelled); Warehouse Waiting (fulfillment Pending, 940 Generated, 940 Sent or 940 Accepted); Invoice Work (invoice Waiting for Delivery Confirmation, Ready, Queued or Failed). Each card opens its list; historical orders (FR-OB-17) are excluded from open-work cards. |
| FR-OB-53 | Logs are detailed enough to find the root cause of any issue (#19): every cycle (start, end, counts), every file and order processing step with its result, every error with full context (file, order, payload reference, message), and every user action on orders and configuration (status change, hold release, Send 940 now, delivery date, invoice edit) with user and time. Logs are searchable in the UI by order, file and date. |
| FR-OB-54 | Replace developer-only functions (resend file, resend invoice email; recalculation and adjustments in the next version) with permission-controlled UI actions. |
| FR-OB-55 | Provide the existing print templates: invoice `IN-<internal number>` (date from the confirmed delivery date, amounts from the invoice columns, letterhead from configuration); purchase order `PO-<retailer PO>`; packing list `PL-<internal number>` with ordered, shipped and remaining quantities and a per-lot breakdown; internal audit packet (summary, addresses, PO lines, pick lines, 945 lots, invoice preview, EDI file audit, sign-off block); transfer order (external and internal). The order's "Files" action emails the PO, invoice and packing list to the requesting user, and every file-register row opens its original file. |
| FR-OB-56 | Provide a **notification management page** where authorised users configure, per event type (unmapped item, parse/fetch/send failure, 997 rejection or timeout, 945 validation (FR-OB-25d), paid order archived before its 940 (FR-OB-03d), transfer partial receipt or discrepancy (FR-OB-84), PO change after 940, cancellation/return after shipment, missing delivery date, invoice emails), the recipients and channel (email, Task Inbox), and see the history of notifications sent (#18). **While there is a single user (D21), every notification and every Action Required task goes to that user**; no event may be routed only to a role that nobody holds. |

### 10.7 Roles and permissions

**At go-live a single user holds all seven permissions** (D21, OB-OI-37). The permissions stay separate, so giving them to more people later is only an assignment and needs no code change; the "Suggested holders" column is guidance for that later split.

| ID | Permission | Covers | Suggested holders |
| --- | --- | --- | --- |
| FR-OB-60 | Read (`sales.read`) | View orders, transfers, files, dashboards, logs | Sales, logistics, finance |
| FR-OB-61 | Order operate (`sales.order.operate`) | Release holds, Send 940 now, confirm delivery and delivered quantity, create and submit manual orders, resolve review items (map an item, confirm a case pack), record cancellations/returns, cancel orders | Order operations |
| FR-OB-62 | File retry (`sales.file.retry`) | Resend or re-parse files, mark as ignored | Order operations, IT |
| FR-OB-63 | Transfer manage (`sales.transfer.manage`) | Create, send and cancel transfers (943) and review 944 receipts | Logistics |
| FR-OB-64 | Inventory adjust (`sales.inventory.adjust`) — **next version** | Adjustments, posting, reversal, inventory re-evaluation (section 10.5) | Logistics supervisor |
| FR-OB-65 | Invoice manage (`sales.invoice.manage`) | Edit invoices, invoicing, resending invoice emails | Finance AR |
| FR-OB-66 | Configuration (`sales.config`) | Partners, items, DC routing, connections, folders, schedule, notifications | IT / system administrator |

### 10.8 File intake and document store

| ID | Requirement |
| --- | --- |
| FR-OB-70 | **Store before parsing.** Every inbound document is stored in the document store before it is parsed, and a document that cannot be parsed is still stored and raises a task. (37 of 73 archived 850s are not valid JSON because Note1 carries literal CR/LF; a reader that parses first would lose exactly these.) Text is decoded as UTF-8, tolerantly, keeping accented characters. |
| FR-OB-71 | **Two identities.** (1) A file already downloaded is not downloaded again, matched on filename, size and remote modified time. (2) Identical content is stored once, matched on the **sha256 of the original bytes** with a unique index; a second file with identical bytes under another name is recorded as a duplicate and not applied again. (70 Radiant files — 27 945s and 12 997s among them — arrive byte-identical under different names.) Replaces Zoho's "file name + modified time + system" key. |
| FR-OB-72 | **Order of operations.** Read, store, commit, and only then apply the connection's after-read action on the partner's server. An after-read action that fails never fails the pass: the document stays stored and is recognised as a duplicate next time. |
| FR-OB-73 | **Files still arriving.** A file is not read until its modified time is at least 60 seconds old (configurable per connection); younger files are left for the next pass. |
| FR-OB-74 | **After-read policy per connection:** Leave, Move (to the connection's processed folder) or Delete. A file that could not be stored is moved to the connection's error folder whatever the policy. A path that is a partner's live folder is never moved or deleted from (policy Leave). A connection test lists folders without creating, moving or deleting anything. During the shadow run all policies are Leave, because Zoho must still see the files (section 11). |
| FR-OB-75 | **Classification by content.** JSON is classified by its top-level key (PurchaseOrder → 850, Invoice → 810); X12 by ST01, never by file name. Each source has a known set of document kinds (Radiant: 945, 944, 997; TrueCommerce: 850; Shopify: orders). Anything else, including our own 940s and 943s reflected back in Radiant's folder, is counted as *not ours* and not read as inbound. |
| FR-OB-76 | **Pass report and batch size.** Every pass records how many files were listed, too new, already downloaded, not ours, stored, duplicates and failed; listed equals the sum of the other six. There is no fixed per-pass cap (#14). If a batch size is ever configured, files already read are skipped before it is applied, files are taken oldest first, and the remaining files are processed immediately in the next pass so nothing falls behind. |

### 10.9 Transfers (943 / 944)

Transfers to Radiant are in this version, so nothing stays in Zoho at cutover. The inventory ledger stays deferred (section 10.5); the 944 receipt lines stored here are its future input.

| ID | Requirement |
| --- | --- |
| FR-OB-80 | **Create and send a transfer.** An authorised user (FR-OB-63) creates a transfer (from and to warehouse, lines of item and quantity in EA) as **Draft**, then sets it **Ready To Send**. Status flow: Draft → Ready To Send → 943 Generated → Waiting for 997 → Waiting for 944 → Received / Partially Received / Discrepancy; Failed on 997 rejection; **Cancelled** is allowed before the 943 is sent, or after it was rejected (Failed); **On Hold** when a second, different 944 arrives (FR-OB-83). Every status change is audited (FR-OB-53). |
| FR-OB-81 | **943.** Generated as in Appendix A.4, with ship-from and ship-to addresses from warehouse configuration (FR-OB-27), a control number from the shared sequence (FR-OB-22) and validation before sending (FR-OB-29a). A failed 943 is not resent automatically; the user resends it (FR-OB-24). |
| FR-OB-82 | **997 for a 943** is matched as in FR-OB-22a. Accepted → Waiting for 944. Rejected → Failed and an Action Required task carrying the AK5 reason code; not resent automatically. The ACK timeout of FR-OB-23 applies. |
| FR-OB-83 | **944 receipt.** Radiant sends **exactly one 944 per 943 transfer** (OB-OI-14); it closes the receipt. It is matched to its transfer by W17-04, and each W07 line is allocated as in Appendix A.7 (line number, then lot, then first line of the same item with remaining quantity). W07 lines repeated per pallet for the same line and lot are **summed, never overwritten** (the A.7 sample has 14 × 600 = 8,400 EA of CF0092 under one lot), and the total must equal W14. A **second, different 944** for a transfer already received is stored but **not applied**; the transfer goes On Hold with an Action Required task showing both 944s (as for 945s, D17/D18). A byte-identical repeat is a duplicate (FR-OB-71) and raises nothing. |
| FR-OB-84 | **Receipt outcome.** Received when received = requested; **Partially Received** when less, with a user notified; **Discrepancy** when anything is over-received, not on the transfer or unrecognised, with an Action Required task listing the lines. A 944 that matches no transfer is stored and raises a task. |

### 10.10 Acceptance criteria

Each criterion is tested before cutover, preferably by **replaying real archived documents** (Shopify pages, 850s, 945s, 997s, 810s) rather than fixtures; fixtures remain useful for fault injection and concurrency. Adapted from the earlier SDM PRD (2026-10-01) to the decisions in this document.

| ID | Criterion | Verifies |
| --- | --- | --- |
| AC-OB-01 | An 850 that is not valid JSON (literal CR/LF in Note1) is stored in its original bytes and raises a task. | FR-OB-70 |
| AC-OB-02 | An address with an accented character (Montréal) stores and re-reads intact. | FR-OB-70 |
| AC-OB-03 | Two files with different names and identical bytes produce one stored document and one reported duplicate, and are applied once. | FR-OB-71 |
| AC-OB-04 | A second pass over an unchanged directory downloads nothing. | FR-OB-71 |
| AC-OB-05 | An after-read action that fails after a successful store leaves the document recorded; the next pass reports a duplicate and does not re-apply it. | FR-OB-72 |
| AC-OB-06 | A file modified less than 60 seconds ago is counted as too new and read on a later pass. | FR-OB-73 |
| AC-OB-07 | A file that cannot be stored is moved to the error folder under every policy; under Leave nothing else on the partner's server changes; a connection test changes nothing. | FR-OB-74 |
| AC-OB-08 | A pass over Radiant's folder containing our own 940s stores none of them and reports them as not ours. | FR-OB-75 |
| AC-OB-09 | In every pass report, listed equals the sum of the other six counts. | FR-OB-76 |
| AC-OB-10 | The Shopify boundary order returns on every fetch and is recognised as a duplicate; a failure part-way through a batch leaves the cursor unchanged; the order query contains no `customer` field. | FR-OB-10 |
| AC-OB-11 | An 850 file with several POs creates every PO. | FR-OB-13 |
| AC-OB-12 | Retailer lines resolve by GTIN, UPC or that partner's buyer part; a buyer part of one partner (e.g. CF0092's Loblaws, Sobeys and Walmart codes) never matches another partner's line. | FR-OB-02 |
| AC-OB-13 | An order with an unrecognised item is created in review, sends no 940, and notifies with the item identifiers received. | FR-OB-15 |
| AC-OB-14 | 10 cases of an item whose case pack is 6 produce `W01*60*EA`; a conversion set without EA = 1 is refused. | FR-OB-02, FR-OB-14 |
| AC-OB-15 | An 850 line whose `ofInnerPacks` differs from the Item Master puts the order in review with a notification; no 940 is sent until resolved. | FR-OB-02 |
| AC-OB-16 | The same order arriving again updates the order held; it never creates a second one. | FR-OB-08, FR-OB-16 |
| AC-OB-17 | A source update two hours into the delay window does not restart the delay, which runs from the paid time (PREPAID) or the receipt time (INVOICE). | FR-OB-03d |
| AC-OB-18 | A source change after the 940 updates the order, keeps the 940 values locked, shows the before/after comparison and raises an Action Required task; no 940 is resent. | FR-OB-16 |
| AC-OB-19 | A manual order in Draft is never queued; after Submit it follows the same 940, 997, 945 and invoice flow as any other order. | FR-OB-19a |
| AC-OB-20 | An unpaid Shopify order is held; when Shopify reports it paid it is released and later queued. | FR-OB-12 |
| AC-OB-21 | A **full-history dry run** produces exactly the agreed eligible set, queues zero historical orders and queues the in-flight unsent orders as expected; one extra order is a stop. | FR-OB-03e, FR-OB-17 |
| AC-OB-22 | A retailer (INVOICE) order with no payment status qualifies; an unpaid Shopify order does not; a `partially_refunded` Shopify order qualifies; a `refunded` one does not. | FR-OB-03d |
| AC-OB-23 | A Shopify order archived during the delay window gets no 940 and the suppression is recorded; an order archived after fulfilment is ignored. | FR-OB-03d |
| AC-OB-24 | A Manual-mode partner's order is never sent automatically; Send 940 now sends it; Send 940 now is refused for a historical order and while a live 940 exists (except a confirmed replacement). | FR-OB-03b, FR-OB-03c |
| AC-OB-25 | For the same order, the generated 940 matches the archived Zoho 940 segment by segment, except control numbers and corrections confirmed with Radiant. | FR-OB-20, Appendix A |
| AC-OB-26 | Every retailer 940 carries N9\*LO and N9\*DP, empty when there is no value; a Walmart order with no DC match goes to review. | FR-OB-03, Appendix A.3 |
| AC-OB-27 | Weight is per can × quantity: two cans at 2.03 lb and one at 2.48 lb declare 6.54 lb. | Appendix A.3 |
| AC-OB-28 | No 940 line is generated for an item the warehouse does not hold. | Appendix A.3 |
| AC-OB-29 | Two concurrent sends never take the same control number; a resend takes a new one. | FR-OB-22 |
| AC-OB-30 | A 940 that fails validation is not sent and notifies a user; an empty country is sent as Canada. | FR-OB-29, FR-OB-29a |
| AC-OB-31 | A 940 sent and not yet acknowledged is shown differently from one accepted. | FR-OB-22a |
| AC-OB-32 | A 997 is matched by AK1 and AK2; AK9/AK5 R is a rejection carrying its reason code on the task, and the 940 is not resent. | FR-OB-22a |
| AC-OB-33 | With no 997 after 2 hours, the 940 is marked Timeout and a task names the order, partner and send time. | FR-OB-23 |
| AC-OB-34 | A pallet with two lots of the same item stores both lots and records 300 ordered once and 298 + 2 shipped. | FR-OB-25b |
| AC-OB-35 | SSCC `00077777770077782891` keeps its leading zeros. | FR-OB-25b |
| AC-OB-36 | Re-applying a 945 (same file or same bytes under another name), including an LTL pallet with no tracking, duplicates no pallet and doubles no quantity. | FR-OB-25, FR-OB-71 |
| AC-OB-37 | A 945 shipping 298 of 300 closes the order's fulfilment and raises one task naming the order, the item and the 2 units; no second 940 is raised. | FR-OB-25 |
| AC-OB-38 | A 945 whose order number carries a suffix (`1000123a`) or zero-padding matches the order; one matching no order is stored and offers "create order". | FR-OB-28, FR-OB-25c |
| AC-OB-39 | The Shopify fulfillment is created when the 945 arrives (with tracking), nothing is written at 997 acceptance, and the order is then archived in Shopify. | FR-OB-11, FR-OB-11a |
| AC-OB-40 | A cancellation before the 940 cancels silently (no email, no task); after the 940, one task and one email to Radiant and the internal owner state the shipped quantity. | FR-OB-18 |
| AC-OB-41 | An order joins the worklist only when its 945 has been applied **and** its requested delivery date is reached: an unshipped order past its requested date is not on the list, and a shipped order before its requested date is not either. A Walmart order uses DoNotDeliverAfter. Neither is invoiced before confirmation. | FR-OB-31 |
| AC-OB-42 | No 810 or invoice email is produced for an order with no recorded delivery confirmation; the invoice states the confirmed quantity, which may be less than shipped. | FR-OB-31 |
| AC-OB-43 | An order unconfirmed for 30 days is still on the worklist and has caused no escalation. | FR-OB-31 |
| AC-OB-44 | Generated 810s match the archived 810s for the same orders (case UOM, 850 unit price, partner terms). | FR-OB-21, FR-OB-30 |
| AC-OB-45 | A partner with invoice method Email cannot be saved without an address; the invoice goes to the retailer with the internal employee in copy. | FR-OB-33, FR-OB-34 |
| AC-OB-46 | Two cycles cannot run at once; a run left RUNNING by a killed process is closed as FAILED by the next cycle. | FR-OB-07 |
| AC-OB-47 | One unreachable partner or one failing order does not stop the cycle for the others. | FR-OB-50 |
| AC-OB-48 | An authentication failure is reported once and not retried on later cycles until a user clears it. | FR-OB-50 |
| AC-OB-49 | A source failing on every cycle has exactly one open task, which identifies the order or document without any lookup. | FR-OB-06 |
| AC-OB-50 | Every failure type in FR-OB-51 produces a notification or task and appears on the health page; no failure is visible only in a log. | FR-OB-51 |
| AC-OB-51 | Every action that changes what is sent or invoiced (configuration edit, Send 940 now, delivery confirmation, invoice edit, task resolution) records the user and time. | FR-OB-53 |
| AC-OB-52 | A fresh deployment sends no email until recipients are configured. | FR-OB-56 |
| AC-OB-53 | A deliberately paused scheduler shows WARNING, and FAILED after the configured duration. | FR-OB-52a |
| AC-OB-54 | No screen shows an inventory figure in this version. | Section 1.2 |
| AC-OB-55 | During the shadow run UniOps writes outbound files only to test folders, and there is no interval in which both systems can send a 940 for the same order. | Section 11 |
| AC-OB-56 | A 945 shipping 310 against a 940 line of 300 is applied as received and sends one "945 validation" notification showing 310 vs 300; the order is not put in review and invoicing is not blocked by the check. | FR-OB-25d |
| AC-OB-57 | A 945 with a W12 item that was not on the 940 sends a "945 validation" notification naming the item, and the unmatched line goes to review (FR-OB-25a). | FR-OB-25d, FR-OB-25a |
| AC-OB-58 | A lot already expired at the ship date, or with less remaining shelf life than the partner's minimum, sends a notification; with no minimum configured only the expired case notifies. | FR-OB-25d, FR-OB-03 |
| AC-OB-59 | A 945 for a cancelled order or an order with no accepted 997 is applied and sends a notification. | FR-OB-25d |
| AC-OB-60 | A 945 whose ship-to or partner differs from the 940 sends a notification showing both values. | FR-OB-25d |
| AC-OB-61 | A 945 with a ship date in the future, or earlier than the 940 send time, sends a notification. | FR-OB-25d |
| AC-OB-62 | A 945 arriving before any accepted 997 sets the 940's ACK status to Accepted by 945 (not Accepted), no Timeout is raised for that file afterwards, and a "945 validation" notification is sent. | FR-OB-25d, FR-OB-23 |
| AC-OB-63 | A paid Shopify order archived before its 940 gets no 940, records the suppression and raises exactly one Action Required task with the order, customer order number, amount paid and archive time; a refunded or voided order archived the same way raises no task. When the order is un-archived it is queued on the next cycle and the task closes automatically; refunding or cancelling it also closes the task. | FR-OB-03d |
| AC-OB-64 | A Draft transfer sends nothing; set Ready To Send, it produces a 943 as in Appendix A.4 with addresses from configuration and a new control number. | FR-OB-80, FR-OB-81 |
| AC-OB-65 | A 997 accepting the 943 moves the transfer to Waiting for 944; a rejecting 997 marks it Failed and raises a task with the AK5 code; the 943 is not resent. | FR-OB-82 |
| AC-OB-66 | A 944 matches its 943's transfer by W17-04; receiving exactly the requested quantity marks it Received. | FR-OB-83, FR-OB-84 |
| AC-OB-67 | A 944 receiving less than requested marks the transfer Partially Received and notifies; an over-receipt or an item not on the transfer marks it Discrepancy with an Action Required task listing the lines. | FR-OB-84 |
| AC-OB-68 | A second, different 944 for a transfer already received changes no receipt quantity, puts the transfer On Hold and raises an Action Required task showing both 944s; re-applying the same 944 changes nothing. | FR-OB-83 |
| AC-OB-69 | A transfer can be cancelled before its 943 is sent, or after it was rejected (Failed), and not otherwise. | FR-OB-80 |
| AC-OB-70 | A `partially_refunded` Shopify order ships only the remaining quantity; a gift card or non-shipping line never appears on a 940; a line refunded after the 940 raises the FR-OB-16 source-change task. | FR-OB-11b |
| AC-OB-71 | Before the channel switch (section 11 step 3), each listed event type has configured recipients and a received test notification. | Section 11 |
| AC-OB-72 | A Shopify order migrated as 940 Accepted, with a Zoho-created fulfillment, gets the 945 tracking added to that fulfillment and the customer notified; no second fulfillment is created. | FR-OB-11 |
| AC-OB-73 | A second, different 945 for an order already Shipped is stored, changes no quantity, puts the order On Hold with an Action Required task showing both 945s, and sends the 945 validation notification; a byte-identical repeat raises nothing. | FR-OB-25, FR-OB-25d |
| AC-OB-74 | A PREPAID order paid two days after it was received is released from Payment Hold and waits the partner's full 940 delay from the payment time, not from receipt. | FR-OB-03d, FR-OB-12 |
| AC-OB-75 | In the A.7 sample, the 14 W07 lines of 600 EA of CF0092 under one lot are summed to 8,400 EA on transfer line 1 (none overwritten), and the 944 total equals W14 (15,600). | FR-OB-83 |
| AC-OB-76 | The real 944 16468 against 943 transfer 00104 marks the transfer Discrepancy and raises an Action Required task listing CF0103 7,200 EA received but not on the transfer and CF0093 0 of 7,200 EA received; line 1 (CF0092 8,400 EA) is Received. | FR-OB-84 |
| AC-OB-77 | A 945 with a DTM ship date records that date; a 945 without a DTM value records the G62 date. | FR-OB-25b |
| AC-OB-78 | A 945 for `1000123b` matches order 1000123; a number with two trailing letters, or whose base number matches no order, goes to review. | FR-OB-28 |
| AC-OB-79 | A Shopify line of 3 × 2-Pack ships 6 EA and 2 × Case ships 12 EA, by the Item Master conversions; a Shopify unit with no conversion, or a conversion that gives a fraction of an EA, sends the order to review. | FR-OB-02, FR-OB-14, FR-OB-15 |

**Replay targets** (checked against the archive before cutover):

| Target | Evidence | Criteria |
| --- | --- | --- |
| Nothing twice | zero duplicate shipments, invoices or quantity applications across the replay | AC-OB-03, AC-OB-29, AC-OB-36 |
| Nothing lost | every archived document is stored and classified | AC-OB-01, AC-OB-08 |
| Right quantity | every reviewed order reconciles to the expected EA | AC-OB-14, AC-OB-15 |
| Traceable | every item-and-lot in the 945 archive is recoverable, including two lots on one pallet | AC-OB-34, AC-OB-35 |
| Historically safe | no already-fulfilled or historical order raises a 940 on replay | AC-OB-21 |
| Invoice gated | no invoice without a recorded delivery confirmation | AC-OB-42 |
| Recoverable | every failure can be investigated and resolved in the application | AC-OB-49, AC-OB-50 |
| Honest screens | every screen uses real data; deferred areas show none | AC-OB-54 |

## 11. Cutover plan

1. **Data migration:** export all orders, lines, lots, transfers and the file register from Zoho; move raw files from WorkDrive into the Order Bridge document store (D22). Partners, items, DC routing and settings are exported from Zoho as CSV and loaded through the UI's CSV import (FR-OB-09). Orders shipped, completed or cancelled in Zoho are flagged historical and become read-only; they never receive a 940 (FR-OB-17). In-flight orders (no 940 yet, or awaiting the 997 or 945) migrate with their status, mapped by section 9.1, and continue their flow.
2. **Shadow run:** only Zoho sends. UniOps pulls the same files (after-read policy Leave, FR-OB-74) and generates 940s and 810s that are written only to the partners' **test folders**, never the live ones, and compared order by order and segment by segment against Zoho. The shadow run ends only when **all** of these hold: (a) **one full cycle of every flow** in scope has passed with no difference — an order arrives, a 940 is built, a 997 and a 945 come back, delivery is confirmed and an invoice is issued, for Shopify and for retailers; (b) the **full-history dry run** of FR-OB-03e produces exactly the agreed eligible set and queues no historical order; (c) **one week** has passed with zero unexplained differences. A calendar week alone is not enough, because a week with no retailer order proves nothing.
   - **Notification gate before step 3:** recipients are configured for every event type in FR-OB-56 — at least authentication failure, 940 failure / rejection / timeout, cancellation after the 940 and paid order archived before its 940 — and a test notification for each has been received. A fresh deployment sends nothing until recipients are set (AC-OB-52), so this gate is what makes failures visible from day one.
3. **Channel-by-channel switch:** Shopify → retailers → transfers. For each channel, stop Zoho's fetch and send so there is no interval in which both systems can send a 940 for the same order; hand over the Shopify cursor; start control numbers above Zoho's highest.
4. **Hypercare:** at least one full ship-to-invoice cycle, with Zoho kept read-only for reconciliation.
5. **Retire Zoho:** rotate SFTP passwords and the Shopify token at this point (no rotation is needed before cutover, #38); remove any credentials the ftp-api.com relay may hold.

Any live file sent to Shopify, retailers or Radiant during testing or shadow running requires explicit approval.

## 12. Related documents

- FINANCE.md — NC remains the financial source of truth; invoices only.
- INVENTORY.md — UniOps inventory views. Order Bridge's inventory ledger is next version (section 10.5); this version records shipment lots and transfer receipts that will feed it.
- PORTAL.md — module entry, permissions and Task Inbox.
- EPMS.md, MRP.md — item and unit-of-measure master data.

## 13. Open Items

Items OB-OI-01 to OB-OI-20 are the open questions from the original PRD; OB-OI-21 onwards came from the review comments. Items resolved in version 1.1 are kept for traceability with the requirement that applies them.

| ID | Item | Status |
| --- | --- | --- |
| OB-OI-01 | Who opens the Zoho Scheduler page, and how often? | Resolved — called externally every 5 minutes; FR-OB-07 |
| OB-OI-02 | Since when has incremental inventory recalculation been disabled, when did the full recalculation last run, and is anyone using the inventory figures? | Open (inventory deferred to next version) |
| OB-OI-03 | Actual effect of the ambiguous operator precedence in Zoho (A && B \|\| C). | Resolved — business rules restated in FR-OB-12, FR-OB-03d, FR-OB-22a, FR-OB-25a and FR-OB-52; verified in the shadow run (FR-OB-03e) |
| OB-OI-04 | Completed count of exactly 8,000: real total or a count cap? | Open |
| OB-OI-05 | Production Radiant SFTP paths. | Resolved — `/prod/in`, `/prod/out` in settings; FR-OB-09 |
| OB-OI-06 | Who enters the delivery date and from what source? | Resolved — manual entry, no automatic source; FR-OB-31 |
| OB-OI-07 | Who releases paid Shopify orders from hold today? | Open (automatic release in FR-OB-12) |
| OB-OI-08 | Should Jean Coutu's invoice email go directly to the retailer? | Resolved — to the retailer's invoice email, internal employee in copy; FR-OB-34 |
| OB-OI-09 | How do sales orders and invoices reach NC — manual entry or an interface? Decides whether UniOps must push orders to NC. | Resolved — no NC interface in this version (D19, section 1.2) |
| OB-OI-10 | Does any retailer order in EA? | Resolved — retailers order in cases; Shopify sells Case, 2-Pack and Each; all converted to EA through the Item Master (FR-OB-02, FR-OB-14) |
| OB-OI-11 | Difference between unit price and invoice unit price; is the invoice edited by hand? | Resolved — invoices are editable; FR-OB-35 |
| OB-OI-12 | Correct CF0110's 11-digit UPC and the placeholder gross weights. | Resolved — corrected by users in the editable Item Master; FR-OB-02 |
| OB-OI-13 | DC routing for non-Walmart retailers and Overwaitea configuration. | Resolved — mandatory for Walmart only, per-partner flag; FR-OB-03 |
| OB-OI-14 | Are Radiant 944s cumulative or incremental? | Resolved — one 944 per 943 transfer; a second, different 944 is stored, not applied, and puts the transfer On Hold (FR-OB-83) |
| OB-OI-15 | Are 945 quantities in EA or cases, and which date is the authoritative ship date? | Resolved — EA (FR-OB-14); ship date from DTM, falling back to G62 (FR-OB-25b) |
| OB-OI-16 | ISA/GS version mismatch and 943 functional-group code: what does Radiant require? | Resolved from evidence — Radiant processed our 943 (ISA 00401, GS 006030, GS01 = OW) and its own 944 uses the same versions; keep them (Appendix A.2, A.4, A.7) |
| OB-OI-17 | Which carrier is actually used (Purolator vs Canada Post)? | Resolved — Purolator for Shopify; retailers LTL / WIFS (FR-OB-11) |
| OB-OI-18 | Obtain the full 850 and 810 JSON schema and samples from TrueCommerce. | Resolved — 850 and 810 are TrueCommerce JSON; the archived 850 and 810 files are the reference samples for Appendix A.8/A.9 and the replay tests; no separate schema needed |
| OB-OI-19 | Will Radiant accept a new control-number range? | Resolved — no limit or issue; FR-OB-22 |
| OB-OI-20 | Rotate credentials now; who runs ftp-api.com and does it retain customer data? | Resolved — risk accepted until cutover: plain-text credentials (section 3.5 item 9) stay until Zoho retirement, when they are rotated (section 11); ftp-api.com dropped (FR-OB-04) |
| OB-OI-21 | Production 940 delays. | Resolved — per-partner configuration set in the UI at go-live, not fixed in the PRD; FR-OB-03a |
| OB-OI-22 | Partner add/edit, manual sending mode, Send 940 now (before delay and after 997 rejection). | Resolved — FR-OB-03, FR-OB-03b, FR-OB-03c |
| OB-OI-23 | Email required for the email invoice method. | Resolved — FR-OB-33 |
| OB-OI-24 | Unmapped items notify with full details. | Resolved — FR-OB-15 |
| OB-OI-25 | Historical Shopify cut-off. | Resolved — old orders are never fulfilled: cut-off kept plus eligibility gate; orders shipped, completed or cancelled in Zoho are historical, in-flight orders continue; FR-OB-17, FR-OB-03d, FR-OB-03e |
| OB-OI-26 | Warehouse-modified order numbers in 997/945. | Resolved — 945 only: one trailing letter (usually a or b) is stripped for matching (FR-OB-28); 997s matched by control number (FR-OB-22a) |
| OB-OI-27 | Folder locations in settings tables. | Resolved — FR-OB-09 |
| OB-OI-28 | Country defaults to Canada. | Resolved — FR-OB-29 |
| OB-OI-29 | PO updates before/after the 940. | Resolved — FR-OB-16 |
| OB-OI-30 | Retailer cancellations and returns after shipment. | Partly resolved — recorded and notified (FR-OB-19); credit note and inventory return handling still to define |
| OB-OI-31 | Configurable scheduler, lock, no Zoho limits, robust error handling. | Resolved — FR-OB-07, FR-OB-50 |
| OB-OI-32 | Failed 940s: notify instead of automatic retry. | Resolved — FR-OB-24 |
| OB-OI-33 | Notification management page. | Resolved — FR-OB-56 |
| OB-OI-34 | Comprehensive logs for root-cause analysis. | Resolved — FR-OB-53 |
| OB-OI-35 | Drop first-time seed data. | Resolved — no seed function or configuration data in code; CSV import/export in the UI; FR-OB-09 |
| OB-OI-36 | Inventory re-evaluation and phase placement. | Resolved — deferred to next version; section 10.5 |
| OB-OI-37 | Assignment of people to each permission (section 10.7). | Resolved — one user holds all permissions at go-live; more users later by assignment only (D21) |
| OB-OI-38 | Completing our data from Radiant (next version): which data Radiant provides, in what format, and how it is reconciled with Order Bridge records. | Open — next version (section 1.3) |
| OB-OI-39 | Transfers (943/944): are they in this version? If they are deferred, how are transfers to Radiant done once Zoho is retired? | Resolved — in this version; section 10.9 |

### 13.1 Decisions (2026-10-20)

The product owner decided the review points below on 2026-10-20 (D1–D16), 2026-10-21 (D17–D20) and 2026-10-23 (D21–D22); they are applied in the requirements shown. None remains open.

| ID | Decision | Applied in |
| --- | --- | --- |
| D1 | "Paid" depends on the partner's settlement mode: PREPAID (Shopify) must be paid; INVOICE (retailers) skips the check; `partially_refunded` ships, `refunded` does not | FR-OB-03, FR-OB-03d, FR-OB-12 |
| D2 | Cancellation after the 940: Action Required plus one email to Radiant and the internal owner stating the shipped quantity | FR-OB-18 |
| D3 | Requested delivery date → worklist → person confirms delivery and delivered quantity → invoice; Walmart uses DoNotDeliverAfter; no automatic escalation | FR-OB-31 |
| D4 | 940 delay and sending mode are per-partner configuration | FR-OB-03a, FR-OB-03b |
| D5 | Archived during the delay window suppresses the 940, and a paid order archived before its 940 also raises one Action Required (refunded or voided: no task); archived after fulfilment is ignored; UniOps archives the order in Shopify after fulfilment | FR-OB-03d, FR-OB-11a |
| D6 | After the 940, the order is updated and a user notified; the 940 values stay locked on the 940 record for the comparison; Action Required raised | FR-OB-16 |
| D7 | Unmatched item: the order is created and goes to review, with the detailed notification | FR-OB-15 |
| D8 | Item Master is authoritative for case pack; a differing 850 `ofInnerPacks` sends the order to review | FR-OB-02, FR-OB-14 |
| D9 | Short shipment closes the order's fulfilment and raises Action Required for the shortfall | FR-OB-25 |
| D10 | One shared control-number sequence, unique and never reused | FR-OB-22 |
| D11 | Inventory (ledger, dashboard) and Amazon FBA are out of this version | Sections 1.1–1.3, FR-OB-52 |
| D11-transfers | Transfers to Radiant (943 out, 997 and 944 in) are in this version, so nothing stays in Zoho at cutover; inventory stays deferred | Section 1.1, section 10.9 (FR-OB-80 to FR-OB-84), FR-OB-63, section 11 step 3 |
| D12 | Old orders are never fulfilled: cut-off plus eligibility gate; Zoho-completed orders historical; in-flight orders continue | FR-OB-17, FR-OB-03d, FR-OB-03e |
| D13 | Shadow-run exit: one full cycle of all flows, test folders, full-history dry run, and one week with zero unexplained differences | Section 11 step 2 |
| D14 | Fresh build; SDM not reused | FR-OB-01 |
| D15 | Keep the v1.1 choices: seven permissions, configurable notifications, one schedule, editable connections | FR-OB-07, FR-OB-56, section 10.7 |
| D16 | Shopify fulfillment is created at the 945, then the order is archived in Shopify | FR-OB-11, FR-OB-11a |
| D17 (2026-10-21) | On Hold principle: whenever something is wrong and a person must check it, the order is On Hold (Review, Payment Hold, Archive Suppressed, 940 Rejected/Timeout/Failed, invoice Blocked/Failed); Blocked covers lot exceptions and cancellation/return after shipment | Section 9.1 |
| D18 (2026-10-21) | Exactly one 945 per order: the 945 closes the fulfilment; Partially Shipped is removed; a second, different 945 is stored, not applied, and puts the order On Hold with an Action Required | FR-OB-25, FR-OB-25d, section 9.1 |
| D19 (2026-10-21) | No NC interface in this version | Section 1.2, OB-OI-09 |
| D20 (2026-10-21) | The 940 delay runs from the paid time (PREPAID) or the receipt time (INVOICE); source updates do not restart it | FR-OB-03d, FR-OB-12 |
| D21 (2026-10-23) | Single user at go-live: one user holds all seven permissions and receives every notification and Action Required task; permissions stay separate so more users can be added later by assignment only | Section 10.7, FR-OB-56, OB-OI-37 |
| D22 (2026-10-23) | Raw documents (inbound and outbound files) are kept in Order Bridge's own document store — raw bytes with a content hash, nothing deleted — instead of the shared file service, so a document is stored and committed before any after-read action in one step (FR-OB-70 to FR-OB-72) | FR-OB-05, section 11 step 1 |
| D23 (2026-10-29) | The module is named **Sales** (folders `sales` / `sales-api`, table prefix `sales_`, permissions `sales.*`, role `sales_operator`, host names `sales.canadaroyalmilk.com` / `sales-api.canadaroyalmilk.com`). Order Bridge stays the name of its first part — orders, fulfilment, invoicing and transfers — and of this PRD and its requirement IDs. Inventory stays a separate module; Sales reads stock from it. Transfers (943 / 944) stay in Sales for now | FR-OB-01, FR-OB-60 to FR-OB-66, design §1 X4 |

## Appendix A. Interface specification (as built in Zoho)

This appendix records the message formats the Zoho application produces and reads today (from the exported script, 2026-10-09). FR-OB-20 and FR-OB-21 require them to be kept; where a requirement in section 10 changes a value, the requirement wins.

### A.1 Connections

| System | Endpoint | Folders / version |
| --- | --- | --- |
| Radiant SFTP | `commhub.wheelsgroup.com` | production `/prod/in` (we send), `/prod/out` (we read); seed pointed to `/test/in`, `/test/out` |
| TrueCommerce SFTP | `sftp.truecommerce.com` | read from `Export`, send to `Import` |
| Shopify Admin API | `https://<store>/admin/api/<version>/orders.json?status=any&updated_at_min=<cursor>&order=updated_at asc` | version `2026-04` |

### A.2 X12 envelope (our 940 and 943)

- `ISA*00*          *00*          *ZZ*CANADAROYALMILK*12*9055651212*<date>*<time>*U*00401*<control>*0*P*>` — sender ZZ/CANADAROYALMILK, receiver 12/9055651212, ISA15 = P (production).
- `GS*OW*CANADAROYALMILK*9055651212*<date>*<time>*<control>*X*006030` — ISA version 00401 with GS version 006030, and GS01 = OW on the 943 as well (the standard would be AR). **Kept as is:** Radiant processes these files and uses the same ISA 00401 / GS 006030 pairing in its own 944 (sample in A.7; OB-OI-16).
- Radiant's GS06 and ST02 are not zero-padded (e.g. `16468`) while its ISA13 is (`000016468`); matching normalises leading zeros (FR-OB-22a).
- Separators: element `*`, segment `~` followed by a newline, sub-element `>`.
- Zoho uses one 9-digit zero-padded number for ISA13, GS06 and ST02 (the order number for a 940, the transfer number for a 943). UniOps keeps one shared 9-digit sequence for all outbound X12 but allocates it independently of order and transfer numbers, so resends and equal order/transfer numbers never collide (FR-OB-22). W05 keeps the order number (A.3).

### A.3 940 shipping order

| Segment | Shopify order | Retailer order |
| --- | --- | --- |
| W05 | `W05*N*<Shopify order number>` | `W05*N*<9-digit internal number>*<retailer PO>` |
| N1 | FR = CANADA ROYAL MILK ULC / CRM; WH = RADIANT COLERAINE WAREHOUSE / CLN02; ST and BT from the order; PER contact sent | same, without PER |
| N9 | `8X*BC`, `E1*<source reference, e.g. SHP-9275>` | `8X*BB`, `E1*CRM-940_…`, `LB*<label code>`, `LO*<DC number>` (sent even when empty), `MR*0020` (Walmart order type 0020 only), `DP*<department>` (sent even when empty) |
| G62 | 01 = requested delivery date, 10 = today | also 38 = today, 79 = requested delivery date |
| W66 | `W66*PP*U***GND*****PRLA` or `EXP` when the shipping method contains "express" (Purolator) | `W66*PP*LT********WIFS` |
| LX / W01 | `W01*<qty EA>*EA**VN*<Item_Code>*BP*<buyer part>` | same, plus line-level N9s with item name, GTIN and buyer part |
| W76 | total quantity and total gross weight in LB (quantity × gross weight per can) | same |

Label code: `UCC_CRM_WMT` for Walmart, otherwise `UCC_CRM_LBW`. Items the warehouse does not hold (for example the bib CRMMARK002) are left off the 940. The source reference is the partner abbreviation, a hyphen and the source order number.

### A.4 943 transfer

- `W06*F*<5-digit transfer no>*<today>*<5-digit transfer no>`; N1 SF and ST from the transfer's warehouses (addresses hard-coded in Zoho: Kingston, 1680 Venture Dr → Caledon, 12480 Coleraine Drive; moved to configuration by FR-OB-27); W27; lines `W04*<qty>*EA**VN*<Item_Code>` plus `N9*LI*<sequence>` with no lot (quantities written with two decimals, e.g. `8400.00`); `NTE*OTH*Transfer Order - TO-<transfer no>_<file name>`; `W27*M*LT*PP`; `W03*<total qty>*<total weight>*LB`.
- Worked example — transfer 00104 as sent by Zoho on 2026-10-07 (Radiant accepted it and returned 944 16468, A.7):

```
ISA*00*          *00*          *ZZ*CANADAROYALMILK*12*9055651212     *261007*1009*U*00401*000000104*0*P*>~
GS*OW*CANADAROYALMILK*9055651212*20261007*1009*000000104*X*006030~
ST*943*000000104~
W06*F*00104*20261007*00104~
N1*SF*Canada Royal Milk ULC*94*CRM~
N3*1680 Venture Dr~
N4*Kingston*ON*K7P 0E9*CA~
N1*ST*Radiant Coleraine Warehouse*94*CLN02~
N3*12480 Coleraine Drive~
N4*Caledon*ON*L7E 4M2~
NTE*OTH*Transfer Order - TO-00104_CRM-943_000000104_TRID_00104_.EDI~
W27*M*LT*PP~
W04*8400.00*EA**VN*CF0092~
N9*LI*1~
W04*7200.00*EA**VN*CF0093~
N9*LI*2~
W03*15600.00*34908.00*LB~
SE*16*000000104~
GE*1*000000104~
IEA*1*000000104~
```

### A.5 997 functional acknowledgement

- AK1 = functional group and group control number (our GS06); AK2 = transaction set and control number (our ST02); AK5 = transaction response and reason code; AK9 = group response.
- AK9 A or E → accepted; P, R or anything else → rejected. Zoho matches on AK1 only and ignores AK2/AK5; UniOps follows FR-OB-22a.
- Zoho treats a 940 as acknowledged when its 945 arrives without a 997. UniOps records this as the separate ACK status Accepted by 945 (FR-OB-25d).

### A.6 945 shipment confirmation

- W06-02 (or W06-06) = order key (FR-OB-28). LX opens a pallet; `MAN*GM*<SSCC-18>`; each `W12*<code>*<ordered>*<shipped>*<diff>*EA*<UPC/GTIN>*VN*<Item_Code>` is one item-and-lot; N9 carries lot (and 2I tracking, MB carrier reference, BM bill of lading); the ship date is taken from DTM, falling back to G62 when DTM has no value (FR-OB-25b); expiry comes with the lot; W27 carries carrier and mode.
- Example: `LX*1  MAN*GM*00077777770077782891  W12*CP*300*298*0*EA*628347940140*VN*CF0092 (lot 26C141125110131, exp 20280519)  W12*CP*300*2*0*EA*628347940140*VN*CF0092 (lot 26E037125110051)` — 300 ordered once, 298 + 2 shipped (FR-OB-25b).

### A.7 944 receipt confirmation

- W17-04 = transfer number. Each W07 receipt line is allocated to transfer lines of the same item with remaining quantity, in priority: LX line number = transfer line number; same lot (N9\*LT); first line of the same item with remaining quantity. Over-receipts, items not on the transfer and unrecognised items become Discrepancy lines.
- Outcome: lines Open / Partially Received / Received / Discrepancy; transfer Discrepancy if anything is unallocated or over-received, Received if received = requested, Partially Received if less, Waiting for 944 if nothing.
- Worked example — Radiant's 944 16468 for transfer 00104 (2026-10-08), abridged: the LX / W07 / G69 / N9\*LT block is repeated once per pallet — 14 times with `LX*1` (600 EA each of CF0092, one lot) and 12 times with `LX*2` (600 EA each of CF0103); SE\*113 counts all segments of the full file:

```
ISA*00*          *00*          *12*9055651212     *ZZ*CANADAROYALMILK*261008*0624*U*00401*000016468*0*P*>~
GS*RE*9055651212*CANADAROYALMILK*20261008*0624*16468*X*006030~
ST*944*16468~
W17*F*20261008*00104*00104~
N1*SF*CANADA ROYAL MILK ULC*94*CRM-CLN02~
N3*1680 VENTURE DR~
N4*KINGSTON*ON**CA~
N1*WH*WAREHOUSE*93*CLN02~
G62*BS*20261008~
LX*1~ W07*600*EA*628347940140*VN*CF0092~ G69*NIURISS STAGE 1~ N9*LT*26C253125110231~   (x14)
LX*2~ W07*600*EA*628347940362*VN*CF0103~ G69*NIURISS SMART 2~ N9*LT*26E248121010211~   (x12)
W14*15600~
SE*113*16468~
GE*1*16468~
IEA*1*000016468~
```

- This real pair is a **discrepancy**: line 1 receives 14 × 600 = 8,400 EA of CF0092 as requested, but line 2 of the 943 asked for 7,200 EA of **CF0093** and the 944 reports 7,200 EA of **CF0103** (UPC 628347940362) under `LX*2`. CF0103 is not on the transfer, so the transfer is Discrepancy (FR-OB-84, AC-OB-76).
- Zoho replaces all receipt lines on every 944. UniOps applies the one 944 per transfer, sums repeated W07 lines per line and lot, and stores any later, different 944 without applying it (FR-OB-83).

### A.8 850 JSON (TrueCommerce, inbound)

- Reference samples: the archived 850 files (OB-OI-18); they are also the replay input for the acceptance tests.

- Partner: `PurchaseOrder[0].TradingPartner` = partner code (e.g. Loblaws `AC-9898`).
- Header: PurchaseOrderNumber (dedup key with partner), PODate, RequestedDeliveryDate (falls back to DoNotDeliverAfter), CancelDate, OrderType, DepartmentNumber, ShipToCode (GLN) / name / address, BillTo\*, buying-party contact, Note1–Note4. Parsed but not used: ShipDate, ShipNoLaterThan, Routing, SCAC, Terms, Agreement ID, EDIMailbox, MarkFor.
- Lines: Line, VendorPart, BuyerPart, UPC, GTIN#, Quantity, UOM, UnitPrice, ofInnerPacks.

### A.9 810 JSON (TrueCommerce, outbound)

- Shape `{"Invoice":[...]}`; AccountingID = partner code; Invoice = internal order number; InvoiceDate = ShipDate = delivery date; SCAC `WIFS`; CarrierPro `CRM|<number>`; tax, charges and allowances empty.
- Line QuantityShipped = (shipped − cancelled − backordered), converted back to the order unit by case pack and truncated to an integer.

### A.10 File names

| File | Name |
| --- | --- |
| Shopify page archive | `<yyyyMMdd-HHmmss>-<first order>-<last order>-COUNT-<n>.json` |
| 850 after order creation | `850-<partner abbrev>-<internal number>-<PO>-<time>.json` |
| 940 | `940-<partner abbrev>-<internal number>-<source number>-<yyyyMMdd_HHmmss>.edi` |
| 943 | `CRM-943_<control>_TRID_<transfer no>_.EDI` |

### A.11 Shopify order mapping (summary)

- id → source order id (dedup key; a page with a missing id fails); name without `#` (else order_number) → source order number; currency (default CAD); created_at → order date; financial_status and fulfillment_status stored (Zoho also writes financial_status into order type — not carried over); cancelled_at / cancel_reason; shipping_lines[0] code or title → shipping method; shipping address → ship-to (province and country codes preferred); billing address → bill-to (replaced by ship-to when missing or outside Canada); contact email/phone; note plus email, phone, shipping method and cancel reason combined into notes.
- Lines: line number = Shopify line id; SKU → vendor and source item number; variant title (case-insensitive contains, first match): "Case" → 6, "2-Can" → 2, "Can" → 1 — in UniOps only a migration and matching hint; the Item Master conversion decides (FR-OB-02). **UniOps rule (product owner decision, 2026-10-30): for Shopify the variant title decides the unit.** If the title names a pack unit of the item — by the unit's name or one of the Shopify titles the Item Master lists for that unit (e.g. 2PACK: "2-Can", "2-Pack"), compared ignoring case, spaces and punctuation, longest first — the line is in that unit; in every other case it is EA ("Bib", "Can", "Default Title"). Safety net: a title with a number of 2 or more and a pack word (case, pack, box, can/cans, ct, x) that matches none of the item's units ("Box of 12", "3 x Can") sends the order to review, so a new pack size never ships as 1 EA. The factor always comes from the Item Master. A retailer 850 line's unit comes from its UOM, never from a title; a line without one goes to review. Item matching order: GTIN → UPC → SKU → Item_Code, active items only. **UniOps (product owner, 2026-10-30): SKU → the variant's barcode as GTIN → as UPC → review;** no item-code fallback. The order line has no barcode, so when the SKU matches no active item the variant is read from Shopify (read_products), once per variant per run; a failed read sends the line to review with the reason. Barcodes compare in GTIN-14 form (FR-OB-02).
- This is Zoho's current rule. In UniOps the unit (Case / 2-Pack / Each) and its EA conversion come from the Item Master (FR-OB-02); the variant title is only a matching hint.

### A.12 Email messages (Zoho)

| Trigger | Subject |
| --- | --- |
| Invoicing (PO, invoice and packing-list PDFs) | Order Documents \| partner \| CRM number \| PO number |
| File failure / 997 rejection | `Order Bridge File Failure - <file name>` (Zoho sends two per failure; UniOps sends one) |
| Integration error | `Integration Error - <function name>` |
