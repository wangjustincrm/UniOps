# Order Bridge — technical design

- **Date:** 2026-10-23
- **Module:** Sales (folders `sales` / `sales-api`, prefix `sales_`); Order Bridge is its first part (orders, fulfilment, invoicing, transfers). Renamed 2026-10-29 (PRD D23, X4 revision); the file name keeps "order-bridge" for history.
- **Status:** Accepted for build (not committed). Decisions X1–X4 accepted 2026-10-23; X4 revised 2026-10-29 (module renamed Sales).
- **Requirements:** `docs/prd/ORDER-BRIDGE.md` 1.2 on `dev/amir` (working tree of 2026-10-23, including the uncommitted open-item and D21 changes). Requirement IDs (FR-OB-xx, AC-OB-xx, OB-OI-xx, Dn) refer to that document; this design does not restate business rules, it says how they are built.
- **Platform:** an ABP-inspired foundation (`sales-api/app/platform/`) and a UI library inside the front end (`sales/src/platform-ui/`), used only by Order Bridge first and kept extraction-ready (§13, isolation rules §14).
- **Pattern followed:** the Inventory module (`inventory-api` + `inventory`), the newest standalone module, and the shared conventions in `packages/authz`, `packages/shell`, the Portal launcher and the Task Inbox outbox. The earlier SDM code (`sdm/`, `sdm-api/`) is not reused (D14); its documents were read as background only.

## 1. Design decisions

These are design choices the PRD does not settle. All four were **accepted as recommended on 2026-10-23**.

| # | Question | Decision | Why |
| --- | --- | --- | --- |
| X1 | **Accepted 2026-10-23.** Where are raw documents stored? FR-OB-05 says "the UniOps file store", but file-api today has no content hash, rejects JSON and X12 content types, accepts only an end-user JWT (a background job has none), streams nothing and runs on a separate file server. | Store raw bytes in sales-api's own table `sales_documents` (bytea, sha256 unique). Files are small (EDI and JSON pages are kilobytes; about 30 orders a day). Generated PDFs (invoice, packing list) are produced on demand, not stored. | Meets FR-OB-70/71/72 (store before parse, sha256 dedup, commit before the after-read action) inside one transaction, which file-api cannot do. Alternative: extend file-api with a service identity, sha256, JSON/X12 types and a list endpoint — larger change in a shared service. |
| X2 | **Accepted 2026-10-23.** Who receives tasks while there is one user (D21)? | A setting `operator_user_id` (the single user). Every Order Bridge task is created with `assigned_user_id` = that user and `assigned_role` = `sales_operator` (a new role code, so the tasks are not broadcast to every system_admin). Notification rules (FR-OB-56) default to that user. | D21 says everything goes to that user and nothing may be routed to an unheld role. |
| X3 | **Accepted 2026-10-23.** Which mailbox sends external email (Radiant cancellation notice, retailer invoice email, failed-940 email)? | The outbound `po_smtp_*` settings in `company_config` (the account already used to send POs to suppliers), with a configurable From/Reply-To for Order Bridge. Internal notifications go through the Task Inbox outbox. | External partners should see the company's outbound address, not the internal notifier. **Revised 2026-11-02 (P6): env** — `SALES_SMTP_HOST/PORT/USER/FROM/STARTTLS` and the password in `SALES_SECRET_SMTP`, to keep the module boundary (sales-api does not read epms' `company_config`); the release copies the `po_smtp_*` values (DEPLOYMENT.md §4b). |
| X4 | **Accepted 2026-10-23; revised 2026-10-29 (module renamed Sales, PRD D23).** Public host names | `sales.canadaroyalmilk.com` (front end) and `sales-api.canadaroyalmilk.com` (API), with DNS A records arranged by the user, as for Inventory. Revision: the module is **Sales** (folders `sales/` and `sales-api/`, table prefix `sales_`, permissions `sales.*`); Order Bridge is its first part (orders, fulfilment, invoicing, transfers). Inventory stays a separate module and Sales reads stock from it; transfers (943/944) stay in Sales for now. | Same pattern as every module; the module will hold more than the Order Bridge feature. |

X1 is recorded in the PRD as D22: FR-OB-05 now reads "archived in the Order Bridge document store", and section 11 step 1 and the settings-key note follow.

## 2. Module, service and wiring

| Item | Value |
| --- | --- |
| Back end | `sales-api/` — FastAPI, Python 3.12, port **8014** |
| Front end | `sales/` — Vite + React + TypeScript + `@uniops/shell`, port **5182** |
| Database | Shared `epms` database, `public` schema, every table prefixed **`sales_`**; Alembic version table **`alembic_version_sales`**; revision ids `sales01_…`, `sales02_…` (≤ 32 characters; `ob01_…` is already taken in epms-api) |
| Settings | `app/core/config.py` (pydantic-settings) reads `POSTGRES_*` only (as inventory-api; `DATABASE_URL` is a derived property), `JWT_SECRET_KEY` (required), `ALLOWED_ORIGINS`, `SALES_SCHEDULER_ENABLED` (env: whether this server may run jobs at all; default false), secret names in §5.3, and the two **prod-only outbound switches** of §5.5: `SALES_EXTERNAL_EMAIL_ENABLED` and `SALES_PARTNER_WRITES_ENABLED` (both default false; set to true only in `.env.prod`) |
| Auth | Identity-issued HS256 JWT verified with the shared key (`app/core/security.py`, `deps.py`), permissions through `packages/authz` (`require_permission`) |
| Health | `GET /health`, `GET /health/db` (mounted at root and under `/api/v1`, as inventory-api) |

Places a new service must be added (from the Inventory rollout): `docker-compose.dev.yml` (API block, front-end block, `sales_node_modules` volume, `VITE_SALES_URL` for portal-frontend), `docker-compose.prod.yml` (`sales-api`, `sales-web`, portal-web build arg), `Caddyfile` (two sites), `.env.prod.example`, `.env.lan.example`, `make-stack-env.sh` (`SALES_API_PORT=8014+off`, `SALES_PORT=5182+off`), `migrate-prod.sh` (`sales-api` after `identity-api`), `check-health.sh`, `reset-db.sh`, `run_tests.sh`, `DEPLOYMENT.md`.

**CORS:** add the 5182 origin (dev) and the sales host (prod) to every `ALLOWED_ORIGINS`: the defaults in each `*/app/core/config.py` (12 services), the 12 inline lists in `docker-compose.dev.yml`, `.env.prod.example`, `.env.lan.example`, `epms-api/.env.example`. The front end calls epms-api (permissions, branding), identity (login hand-off) and sales-api; missing origins break branding and permissions silently.

**Portal:** `navConfig.tsx` MODULES entry `{label:'Sales', href:'sales', anyPermission: SALES_ACCESS_PERMS}` where `SALES_ACCESS_PERMS` lists the seven keys of §6; `resolveNavHref` → `${SALES_URL}/#__session=…`; `portal/src/lib/api.ts` `SALES_URL` and `RETURN_URL_ORIGINS`; `PortalHome.tsx` card titled "Sales" with `hasSalesAccess`; `AdminPanel.tsx` `MODULE_TAGLINE_KEYS` gets `{key:'sales', label:'Sales'}`; branding via `useBranding('sales')`.

## 3. Data model

All tables `sales_`. Status values are closed enumerations (PostgreSQL enum types or CHECK constraints) taken from PRD section 9.1 (FR-OB-08). Every table has `created_at`, `updated_at`; user-editable tables also `updated_by`.

### 3.1 Configuration (editable in the UI, CSV import/export — FR-OB-09)

| Table | Key / unique | Main columns |
| --- | --- | --- |
| `sales_partners` | `code` (immutable after first document) | name, abbrev, channel (SHOPIFY / TRUECOMMERCE / MANUAL), settlement_mode (PREPAID / INVOICE), sending_mode (AUTOMATIC / MANUAL), delay_minutes, invoice_method (810_JSON / EMAIL / NONE), invoice_email, invoice_cc, payment_terms, dc_routing_required, min_shelf_life_days (nullable), label_code, carrier defaults, active |
| `sales_items` | `item_code` | description, sku, upc, gtin, gross_weight_lb_per_ea, warehouse_held, active |
| `sales_item_units` | (item_code, unit) | unit (EA / CASE / 2PACK / …), factor_ea; CHECK EA = 1 (FR-OB-02); shopify_titles — optional comma list of the Shopify variant titles that mean the unit (PRD A.11; `sales06`) |
| `sales_item_partner_codes` | (partner_code, buyer_part) unique | item_code (FR-OB-02: buyer parts scoped per partner) |
| `sales_dc_routing` | (partner_code, ship_to_gln) unique | dc_number, notes |
| `sales_warehouses` | `code` | name, address lines, role (SHIPPER / WAREHOUSE / TRANSFER_SOURCE) |
| `sales_connections` | `name` | kind (SFTP / SHOPIFY), host, port, username, folders (inbound, outbound, processed, error, test_inbound, test_outbound), after_read (LEAVE / MOVE / DELETE), stable_seconds (default 60), mode (LIVE / TEST), active, auth_failed_at, auth_failed_reason, secret_name (name of the server secret, never the secret), host_key (the pinned public host key, §5.1; added by `sales03`) ; Shopify per store (option 1, product owner 2026-10-29; `sales04`): api_version (was the SHOPIFY_API_VERSION setting), cursor_updated_at (the FR-OB-10 cursor, was SHOPIFY_LAST_SYNC_DATE; system-written, reset by an audited action, NULL pulls nothing), page_size (≤ 250), order_number_cutoff (the FR-OB-17 cut-off, was a setting) and partner_code (SHOPIFY only) |
| `sales_settings` | `key` | scheduler interval and pause, ACK timeout (120 min), stale-run threshold, operator_user_id, email From/Reply-To, task age for health WARNING |
| `sales_notification_rules` | `event_type` | channel (TASK / EMAIL / BOTH), recipients (user ids, external addresses), active (FR-OB-56) |

### 3.2 Documents and runs

| Table | Key / unique | Main columns |
| --- | --- | --- |
| `sales_documents` | `id`; **`sha256` unique**; (connection_id, remote_name, size, remote_mtime) unique where direction = IN | direction (IN / OUT), origin (PARTNER / UNIOPS / ZOHO_REFERENCE / ZOHO_MIGRATION), system (RADIANT / TRUECOMMERCE / SHOPIFY / EMAIL), connection_id, kind (850_JSON, 940_EDI, 943_EDI, 944_EDI, 945_EDI, 997_EDI, 810_JSON, INVOICE_EMAIL, SHOPIFY_ORDER_JSON, OTHER), remote_name, size, remote_mtime, **content bytea** (X1), body_json (SQL NULL when not parsed), processing_status, file_status, ack_status, isa13 / gs06 / st02, partner_code, error, replaced_by_id, zoho_file_id (migration) |
| `sales_document_links` | (document_id, entity_type, entity_id) | many-to-many to orders and transfers |

**Identity rows** (product owner, 2026-10-29): a file whose bytes are already stored (Duplicate) or that is not ours (Skipped, FR-OB-75) is recorded as a `sales_documents` row **without content** — its remote identity and sha256 only — so the next pass recognises it and downloads nothing (AC-OB-04) while identical bytes are still kept once (AC-OB-03). The sha256 unique index is partial (`WHERE content IS NOT NULL`), and a CHECK allows no content only for those two statuses. A file read again after a failed after-read action is therefore reported as *already here*, not as a duplicate (FR-OB-72's intent: recognised, never applied again).
| `sales_runs` | `id` | started_at, heartbeat_at, finished_at, status (RUNNING / SUCCEEDED / PARTIAL / FAILED), trigger (SCHEDULE / MANUAL), pass report counts per connection (FR-OB-76), error summary |
| `sales_log` | `id` | run_id, level (ERROR / WARNING / INFO), step, document_id, order_id, transfer_id, message, context jsonb (FR-OB-53) |
| `sales_audit` | `id` | user_id, action, entity, before / after jsonb, at (FR-OB-53) |
| `sales_shadow_matches` | (document_id) unique | inbound 997/945/944 received while its channel is in TEST mode, matched_reference_id (a ZOHO_REFERENCE document), what UniOps would have recorded (jsonb), difference, reviewed_by (§5.4) |

### 3.3 Orders

| Table | Key / unique | Main columns |
| --- | --- | --- |
| `sales_orders` | `id`; `order_number` from sequence `sales_order_number_seq` (starts above Zoho's highest); **(partner_code, source_order_id) unique** | source_type, source_order_number, source_reference, order_type, department, dates (order, requested delivery, do-not-deliver-after, cancel), ship-to / bill-to / contact (snapshot), currency, totals, **fulfillment_status**, **invoice_status**, hold_reason, **historical** (bool, FR-OB-17), first_received_at, **paid_at** (delay clock, D20), archived_in_shopify_at, archive_suppressed, shopify_financial_status, shopify_fulfillment_id (AC-OB-72), warehouse_order_number_received (FR-OB-28), zoho_order_id |
| `sales_order_lines` | (order_id, line_no int) unique | item_code, source item fields, ordered_qty, order_unit, factor_ea, qty_ea, unit_price, line_status (Active / Shipped / Short Shipped / Cancelled / Error), shipped_ea, delivered_qty, removed |
| `sales_940s` | `id`; control_number unique | order_id, document_id, status (GENERATED / SENT / ACCEPTED / ACCEPTED_BY_945 / REJECTED / TIMEOUT / FAILED / REPLACED / CANCELLED), sent_at, ack_document_id, ak5_code, **locked_values** jsonb (the item, quantity, unit, warehouse and ship-to that were sent — FR-OB-16), replaced_by_id |
| `sales_shipments` | (document_id) unique | order_id (nullable — FR-OB-25c), ship_date, carrier, mode, tracking, bol, carrier_ref |
| `sales_shipment_rows` | (shipment_id, lx_index, w12_index) unique (FR-OB-25) | sscc (text), item_code, lot, expiry, qty_shipped_ea, qty_ordered_ea, weight, order_line_id (nullable) |
| `sales_delivery_confirmations` | (order_id) unique | confirmed_by, confirmed_date, per-line delivered_qty (FR-OB-31) |
| `sales_invoices` | `invoice_number` (`IN-<order number>`) | order_id, method, invoice_date, totals, status, document_id, edited_by / edited_at (FR-OB-35) |
| `sales_invoice_lines` | (invoice_id, line_no) | qty (case UOM), unit_price, amount |

`sales_x12_control_seq` — one PostgreSQL sequence for all outbound X12 (D10), started above Zoho's highest control number at cutover.

### 3.4 Transfers

| Table | Key / unique | Main columns |
| --- | --- | --- |
| `sales_transfers` | `transfer_number` (5-digit, from Zoho's next) | from / to warehouse, status (Draft … Discrepancy, Failed, Cancelled, On Hold), created_by |
| `sales_transfer_lines` | (transfer_id, line_no) | item_code, requested_ea, received_ea, line_status |
| `sales_receipts` | (document_id) unique | transfer_id, receipt_date, applied (bool — false for a second, different 944, FR-OB-83) |
| `sales_receipt_rows` | (receipt_id, lx_index) unique | item_code, upc, lot, qty_ea, transfer_line_id (nullable), discrepancy_reason |

### 3.5 Tasks

Tasks are rows in the shared `tasks` table (owned by epms-api), written directly as finance-api does (`ap_duplicate_invoice_tasks.py`): `type` = `sales_<event>` (≤ 40 chars), `document_type` = `sales` (the shared column is `String(10)` — epms-api `app/models/task.py`; `sales` is 12 characters and every insert would fail; product owner, 2026-10-28), `document_number` = a **problem key** (`SALES:<event>:<entity id>`, ≤ 40), `title` ≤ 255 (longer raises, it is never swallowed), `assigned_user_id` = operator (X2), `notified_at` NULL so the Task Inbox outbox emails it when the policy is on — **except while `SALES_EXTERNAL_EMAIL_ENABLED` is false**: then the task is written with `notified_at = now()`, so the epms notifier never mails it from a dev or test stack (product owner, 2026-10-28). Before inserting, the service takes a transaction-scoped advisory lock on (`document_type`, problem key), then looks for an open task with the same `document_type` + `document_number` and refreshes it instead (one open task per problem, FR-OB-06, also when two units raise it at once); when the condition clears, the task is completed with `completed_at` (e.g. archived-order task when the order is un-archived — FR-OB-03d). Task writes run in a savepoint so a task failure never fails a run.

### 3.6 Migrations

| Service | Revision | Content |
| --- | --- | --- |
| sales-api | `sales01_config` | partners, items, units, partner codes, DC routing, warehouses, connections, settings, notification rules, enums |
| sales-api | `sales02_audit_outbox` | the platform's `sales_audit` and `sales_outbox` (mixin columns, §13.7 / §13.13) |
| sales-api | `sales03_documents_runs` | documents, links, runs, log |
| sales-api | `sales04_shopify_channel` | the Shopify settings on the connection row (§3.1) |
| sales-api | `sales05_orders` | orders, lines, `sales_order_number_seq` (P3) |
| sales-api | `sales06_unit_shopify_titles` | `shopify_titles` on `sales_item_units` (P3) |
| sales-api | `sales07_user_views` | personal saved views (§13.15) |
| sales-api | `sales08_outbound_940` | `sales_x12_control_seq` (1…999999999), `sales_documents.sent_at` and `ack_detail`, `sales_orders.shipping_method` (P4) |
| sales-api | `sales09_shadow` | `sales_documents.shadow`, `sales_orders.shadow`, `sales_shadow_matches` (shadow run, §5.4; pulled before P5) |
| sales-api | `sales10_shipments` | `sales_shipments` (document + set index unique, order nullable, applied, shadow, checks, Shopify write-back state), `sales_shipment_rows` (shipment + LX + W12 position unique; SSCC text), `sales_shadow_matches.document_id` / `would`, status "No reference" (P5) |
| sales-api | `sales11_invoicing` | `sales_delivery_confirmations` (order unique), `sales_invoices` (`IN-<order number>`, order unique, method, date, terms, totals, document, sent_at, send_count), `sales_invoice_lines` (cases at the 850 price), `sales_order_returns` (FR-OB-19) (P6) |
| sales-api | `sales12_transfers` | `sales_warehouses` 943 party columns (FR-OB-27), `sales_transfer_number_seq`, `sales_transfers` (+ `status_before_hold`), `sales_transfer_lines`, `sales_receipts` (document unique, applied, shadow, outcome, discrepancies), `sales_receipt_rows` (receipt + position unique) (P7) |
| sales-api | `sales13_…` and later | P8 onward |
| identity-api | `0023_sales_perms` | the seven `sales.*` keys in `permission_defs` (module `sales`), granted to `system_admin`; role `sales_operator` in `role_defs` with all seven (D21) |

Renumbered (product owner, 2026-10-29): P1's configuration writes are audited (FR-OB-53), so `sales_audit` cannot wait for the documents migration, and `sales_outbox` was in no migration; both are `sales02_audit_outbox`, and documents / orders / transfers moved to `sales03` … `sales05`; P3 (2026-10-29) added `sales04_shopify_channel` and split the order tables by phase.

Before writing each migration: confirm single head per service and the parent (identity head today: `0022_inventory_perms`; the EHS branch carries its own identity 0016–0020 — coordinate if it merges first), and run the ≤ 32-character check. No seed data in migrations (FR-OB-09): configuration arrives by CSV import.

## 4. Processing

### 4.1 Scheduler

- sales-api gets `app/core/background.py` with the same `spawn(coro, name=)` / `drain(timeout)` contract as epms-api (strong references, exceptions logged, cancel-and-await on shutdown). The scheduler loop is started with `spawn` in the lifespan and stopped by `drain(timeout=10)` **before** `engine.dispose()`; no bare `asyncio.create_task`.
- Loop: fixed tick (30 s); each tick re-reads `interval` and `paused` from `sales_settings` (FR-OB-07; default 5 min; interval change takes effect on the next tick without restart) and runs a cycle when due.
- Single flight: a transaction-scoped advisory lock (`pg_try_advisory_xact_lock(<orders key>)`) around claiming the run, plus an `sales_runs` row in RUNNING with a heartbeat. A run whose heartbeat is older than the stale threshold is closed as FAILED by the next cycle (FR-OB-07, AC-OB-46).
- "Run now" in the UI (permission `sales.file.retry`) claims a run through the same path; it never runs a second cycle concurrently.
- Env switch `SALES_SCHEDULER_ENABLED` must be true for the loop to start (false in dev and test stacks by default, so a developer stack never talks to partners). It says whether this server may run jobs at all; the user's switch is below.
- **Per connection (user decision 2026-11-02; `sales14`, `app/services/cycle.py`).** Two kinds of job replace the one cycle, each a `ScheduledJob` with its own single flight and run records (`sales_runs.scope`):
  - a **connection job** per active connection (`connection:<id>`), due when its `next_run_at` is reached, it is not `schedule_paused`, and the time is inside its optional window — `active_from` / `active_to` in Toronto time (an end before the start crosses midnight and belongs to the day it started) on `active_days` (empty = every day) — `app/domain/schedule.py`. It does the connection's step (SFTP pass, or the store's Shopify pull) and then takes the documents it just stored through (850 → orders, 997 / 945 / 944 applied; only that connection's, `connection_id`), with the shadow rules. Its run writes `last_run_at / status / summary` ("3 files, 0 errors"), `last_error` and `next_run_at` (start + `schedule_minutes`, also after a failure) — a Core UPDATE: no audit row and no new version. `schedule_minutes` defaults to 5 (SFTP) / 10 (Shopify); the schedule is edited on the connection (form and CSV, `sales.config`, audited).
  - the **system job** (`system`), every `scheduler.system_minutes` (default 1): 940 / 943 queueing, invoice advance, the outbox, ACK timeouts, the shadow reference import and comparison.
  - `scheduler.paused` is the user's switch for every job, shown as **Automatic runs: On / Off** (`scheduler.interval_minutes` is no longer read). *(User decision 2026-11-04.)* It defaults to Off, and a bad stored value reads as Off, so a database that never set it starts nothing even with the env on; a stored value still wins, so check it before turning the env on. Off is a normal state with no alarm (the paused-alarm duration is gone); overdue applies only while On. Run now: per connection (`POST /config/connections/{id}/run-now`, `sales.file.retry`; ignores the schedule, refused while that connection runs), the system job (`POST /runs/run-now/system`), and the global one (`POST /runs/run-now`: every active connection — one already running is skipped — then the system job; returns the system run). The switch and the system interval are edited on Connections, the Automatic runs card (`/config/scheduler`, `sales.config`, audited; a PUT changes only the fields it sends; turning On asks first and says what starts; with the env off there is no switch); Health shows the status only: it lists every job with its last and next run and flags one overdue by more than three intervals (WARNING).

### 4.2 One cycle

(Since 2026-11-02 these steps are split between the connection jobs — step 1 and the step-2 documents a connection stored — and the system job — steps 3 to 6; §4.1.) Steps run in order; each unit of work (one connection, one document, one order) runs in its own transaction and `try` block, records its outcome in `sales_log`, raises its task when needed and moves on (FR-OB-50, AC-OB-47):

1. **Fetch** every active connection (SFTP: list → filter → download; Shopify: pages from the cursor). Store each document (FR-OB-70..76, §4.3).
2. **Parse and apply inbound** documents in arrival order: 850 → orders; Shopify pages → orders; 997 → 940/943 ACK; 945 → shipments; 944 → receipts.
3. **Time-based rules:** payment-hold release (FR-OB-12), ACK timeouts (FR-OB-23), worklist entry for delivery confirmation (FR-OB-31), archived-order checks (FR-OB-03d).
4. **Build outbound:** 940s for eligible orders (FR-OB-03d), 943s for Ready To Send transfers, 810 JSON / invoice emails for confirmed orders. Each file is validated (FR-OB-29a) and stored before sending.
5. **Send** outbound documents in TEST or LIVE folders per connection mode (§5.4).
6. **Write back to Shopify:** fulfillment with tracking at the 945 (FR-OB-11, D16), archive the order (FR-OB-11a), add tracking to a Zoho-created fulfillment for migrated orders (AC-OB-72).
7. Close the run with its pass report.

**Authentication-failure latch:** a refused login sets `auth_failed_at` on the connection; later cycles skip that connection and report it on the health page until a user clears it (FR-OB-50, AC-OB-48). Network errors are not latched; they are retried on the next cycle.

### 4.3 Intake pipeline (FR-OB-70..76)

For each listed remote file: skip if (connection, name, size, mtime) is already stored → skip if younger than `stable_seconds` (too new) → download → compute sha256 of the original bytes → insert `sales_documents` (a unique violation on sha256 records a duplicate) → **commit** → apply the after-read action (Leave / Move / Delete; failures only logged) → classify (JSON top-level key; X12 by ST01; our own 940/943 reflected in Radiant's folder = not ours, counted and not stored). Unparseable content is still stored and raises a task. Text is decoded tolerant UTF-8. The pass report counts listed / too new / already here / not ours / stored / duplicates / failed, and listed must equal the sum (AC-OB-09).

### 4.4 Flows

| Flow | Design |
| --- | --- |
| Shopify pull | Admin REST `orders.json?status=any&updated_at_min=<cursor>&order=updated_at asc`, paginated (page_info links), without `customer` fields; inclusive cursor advanced to the batch maximum after commit (FR-OB-10). Each page is stored as a SHOPIFY_ORDER_JSON document. Orders upsert on (partner, Shopify id); below the cut-off → historical. Lines: shippable product lines only, current quantity (FR-OB-11b); unit from Item Master (FR-OB-02); paid_at set when payment becomes satisfied (D20). |
| 850 | Parse every PO in the file (FR-OB-13); partner from `PurchaseOrder[0].TradingPartner`; lines matched GTIN → UPC → partner buyer part; case pack checked against Item Master (FR-OB-02); unmatched → order in Review + task (FR-OB-15); resend after 940 → order updated, 940 values stay locked, task (FR-OB-16). |
| 940 | Eligibility is one SQL predicate plus a Python function that implements FR-OB-03d exactly (both are tested against the same cases — AC-OB-21/22). Builder renders Appendix A.3 from a template per channel; control number from `sales_x12_control_seq`; validator checks mandatory segments, EA quantities, DC rule, country default (FR-OB-29/29a). Send 940 now and replacement resend reuse the same builder (FR-OB-03c). |
| 997 | Parse AK1/AK2/AK5/AK9; match on partner + group + GS06 + ST02, zeros normalised (FR-OB-22a); accepted → 940 Accepted; rejected → 940 Rejected + task with AK5 code; also for 943 (FR-OB-82). |
| 945 | One per order (D18): parse every LX / W12 into shipment rows (FR-OB-25b), order key W06-02/06 with one-letter strip (FR-OB-28), ship date DTM else G62, idempotent by document hash and row position, closes the fulfilment, shortfall task (FR-OB-25), second different 945 stored not applied + On Hold (FR-OB-25), validation notifications (FR-OB-25d), no matching order → stored + "create order" task (FR-OB-25c); shadow run: no task, `app/services/order_from_945.py` makes the order on request (`GET /orders/shipments-without-order`, `POST /orders/shipments/{id}/create-order`, `sales.order.operate`; source_type FROM_945, migration sales20; refused outside the shadow run). |
| 943 / 944 | 943 per Appendix A.4 (NTE, W27, W03 as in the sample); 944 one per transfer, W07 rows summed per line + lot, total checked against W14, allocation and Discrepancy per A.7 / FR-OB-84, second different 944 stored not applied + On Hold (FR-OB-83). |
| Invoicing | Worklist when shipped **and** requested date reached (FR-OB-31); bulk confirmation with delivered quantities; then by partner method: 810 JSON (Appendix A.9) uploaded to TrueCommerce `Import`, or email with invoice / PO / packing-list PDFs (reportlab, as in epms-api `pdf_*`), or none (FR-OB-30..35). Invoice edits audited and resendable. |

### 4.5 Status changes

**Status (PRD 9.1; FR-OB-52).** `app/domain/order_status.py` holds the rules once, as data over an algebra: `status_rules(f, b)` and `hold_rules(f, b)` return (condition, value) pairs. `display()` runs them on plain facts for a row; `app/services/order_status_sql.py` runs the same rules on columns as the CASE that lists filter, sort and group on and Home and Health count with (`order_status`, `on_hold_reason`). A test compares the two over every internal combination. The facts read from other tables are column properties on `Order` (`has_945_sql`, `shopify_failed_sql`, `shopify_pending_sql`, `shadow_short_sql`, `partner_invoice_method_sql`, …). The state machines, eligibility and `order_state` do not change. `fulfillment_status`, `invoice_status`, `order_state` and `hold_reason` stay filterable and sortable, so saved views keep working, but `/meta` no longer offers them.

**Cancelling and releasing (PRD 9.1).** `app/services/order_holds.py`: `POST /orders/{id}/cancel`, `POST /orders/{id}/release-hold`, `POST /orders/shipments/{id}/rows/{row_id}/resolve` (`sales.order.operate`, refused for a shadow order). `order_rules.can_cancel` and `hold_exits` say what is open; the order detail carries them as `cancellable` and `hold_exits`. **945 rows to lines:** `app/domain/row_matching.py`, used by `Shipments.plan` in shadow and applied; `lifecycle.spread` still gives each line's quantity from the item's total, because a row that fits no line cannot be split. **Tracking:** one number goes as REST `tracking_info`, as before; several go through GraphQL (`fulfillmentCreate`, `fulfillmentTrackingInfoUpdate` with `numbers`), because REST takes one. **Open view:** the first preset of Orders, `order_status not_in NOT_OPEN_STATUSES`. `python -m app.scripts.link_shadow_945 --relink` applies the rule to rows linked before it (dry run unless `--apply`).

All status changes go through one module (`app/domain/status.py`, which declares the four PRD 9.1 machines on the platform `StateMachine`, §13.6) that enforces the allowed transitions of PRD section 9.1 and writes `sales_audit` for user actions. Order state is computed (SQL view `sales_order_state_v`), never stored (D17). Unit tests enumerate the transition table.

## 5. Integrations

### 5.1 SFTP

`asyncssh` client (new dependency; no SFTP code exists in the repo). Direct connection to `commhub.wheelsgroup.com` and `sftp.truecommerce.com` (FR-OB-04, no ftp-api.com). Host keys pinned in configuration (first-connect fingerprint shown to the admin for confirmation). Connection test lists folders only (FR-OB-74).

As built in P2 (product owner, 2026-10-29): the gateway (`app/integrations/sftp.py`, one SSH connection per call) refuses **before any network traffic** unless (1) the host is in `SALES_SFTP_ALLOWED_HOSTS` — a comma list required in every environment, empty or unset = no SFTP at all, production included, and not keyed on `ENVIRONMENT`; dev stacks list only their `sftp-test` container — (2) the connection's `host_key` is pinned (no trust on first use) and (3) its secret is set. The connection test reads the server's key without logging in and shows its SHA256 fingerprint; a `sales.config` user pins it (an audited update); folders are listed only when the presented key is the pinned one. A refused login latches the connection (FR-OB-50); a different key, a refused host or a missing secret are ordinary failures. The test and the log show a short readable message (timeout, refused, name not found, login refused, different host key, not an SFTP server, not in the allowed hosts), never the library's raw text; that goes to the server log only.

### 5.2 Shopify

`httpx` client with the client-credentials grant (PRD FR-OB-10): `<secret_name>_CLIENT_ID` and `<secret_name>_CLIENT_SECRET` are exchanged for the 24-hour token, which is cached in memory and renewed 600 s before expiry. Only when the client id / secret are absent does the gateway fall back to a static `shpat_` token in `<secret_name>` (an optional fallback, logged at startup). App scopes needed: read_orders, write_orders (archive), read/write_fulfillments, read_merchant_managed_fulfillment_orders; not read_customers (granted today and the gap: §12 item 8). API version from the connection (2026-04 today).

### 5.3 Secrets (FR-OB-04)

SFTP passwords (or keys) and the Shopify client id/secret (and an optional static token) live only in the server environment (`.env.prod`, injected as env vars named in `sales_connections.secret_name`, e.g. `SALES_SECRET_RADIANT_SFTP`). The database and the UI never hold them; the UI shows "secret present / missing" only. Rotation at Zoho retirement (section 11 step 5) is an env change plus restart.

**Naming (2026-10-29).** A connection's `secret_name` names the env var, and related values hang off it by suffix — for Shopify: `<secret_name>_CLIENT_ID` and `<secret_name>_CLIENT_SECRET` (`shpss_…`) are the client-credentials app the gateway authenticates with (§5.2); `<secret_name>` may hold a static `shpat_…` token, used only as the fallback when the id / secret are absent. The values never appear in the database, the UI, a response, a log line or the repository; dev stacks read them from a mode-600 file outside the repo through a local compose override.

### 5.4 Test vs live (shadow run)

Each connection has `mode` LIVE or TEST. During the shadow run: all inbound after-read policies are Leave (Zoho must still see the files), outbound goes to the test folders (TEST mode) or is generated without sending, Shopify write-back is disabled by a setting, and the comparison job (§10) diffs UniOps 940s/810s with Zoho's archived files. Switching a channel to LIVE is a configuration change logged in `sales_audit`; the go-live gate in PRD section 11 (notification recipients + test notification, AC-OB-71) is checked by the health page before the switch is allowed.

**Acknowledgements and confirmations in shadow mode.** While only Zoho sends, every real 997 Radiant returns acknowledges a Zoho 940/943 with Zoho's control numbers, and every real 945/944 answers a Zoho 940/943. Applied normally, FR-OB-22a ("no match → review + task") would raise a task for every real 997 for weeks, and 945s/944s would be applied to orders whose 940 UniOps never sent. Design:

- A channel in TEST mode keeps a **reference copy of what Zoho sent**: Zoho's outbound 940/943 files are read continuously (from Radiant's inbound folder with policy Leave, or from the Zoho file-register export if Radiant moves them away) and stored in `sales_documents` with `origin = ZOHO_REFERENCE`. They are never sent and never count as a live 940.
- Inbound 997/945/944 for a TEST-mode channel are stored and matched **against the reference copies** (same rules as FR-OB-22a/28/83). The result is written to a comparison table (`sales_shadow_matches`: document, matched reference, what UniOps would have done, difference) and shown on a shadow-run page. In TEST mode these documents **do not change order or transfer status and raise no review tasks**. Only parse failures and intake errors still raise tasks, because they would also be real problems after cutover.
- The comparison job then shows, per order: Zoho's 940 vs the 940 UniOps built; the 997/945/944 that came back; and what UniOps would have recorded. Exit criterion (a) of PRD section 11 step 2 is evaluated on this table.
- If UniOps writes its own 940s to Radiant's `/test/in`, their 997s (if Radiant returns any) arrive in `/test/out` with UniOps' control numbers and are matched normally against UniOps' own TEST 940s.
- **Needs Radiant's confirmation:** whether files in `/test/in` are processed at all, whether they produce 997s (and 945s) in `/test/out`, and that they never ship goods. PRD section 11 step 2 (a) assumes "a 997 and a 945 come back"; if Radiant's test side returns nothing, criterion (a) can only be met from the reference matches above, and the first real UniOps 940/997/945 round trip happens at the per-channel switch (hypercare).

**As built (2026-11-02, pulled before P5 by the user's decision; reduced scope).** Zoho stays the system of record and its delete-after-download is switched off; Sales reads the same live folders.
- Setting `shadow_mode` (audited; `GET/PUT /config/shadow`, `sales.config`; Connections → Shadow run). A bad stored value reads as **on**. Global only: a per-connection override was not built (it would need the order-level flag per connection as well).
- While on: every SFTP pass treats after-read as **Leave** whatever the connection says (logged when the policy differs), and a file that cannot be stored is not moved to the error folder either; every stored document (SFTP and Shopify pages) has `shadow = true`; switching on also marks every open order (not Shipped / Cancelled / historical, no live 940) `shadow = true` in the same unit, audited, with the count logged and returned — switching off never clears it (follow-up 2026-11-02); every order created has `shadow = true` and is never eligible for a 940 — also after the switch-off (`qualifies()` and its SQL twin); Send 940 now is refused (`send940.shadow`, `send940.shadow_order`); generation is refused in `generate()` itself; the outbox's `pre_check` cancels a `send_940` row (second barrier for a row queued before the switch-on); 997s are stored, flagged and set Skipped with the reason — no status change, no task. Only intake / connection problems raise tasks (`SHADOW_RAISED`: unreadable file, fetch failed, login refused, not stored, Shopify fetch / login, unreadable order file); every other problem is logged ("shadow run: no task raised").
- Reference files: `SALES_ZOHO_REFERENCE_DIR` (a read-only folder in the container, mounted only by a local override; empty = off). Run now stores each file once (by SHA-256) as an OUT document with origin `ZOHO_REFERENCE`, `shadow = true`, no file or processing status — never parsed into orders, never sent.
- Comparison (`app/domain/shadow.py`, `app/services/shadow.py`), each Run now: per reference 940 the order by W05 03 (retailer PO, EDI 850 orders) else W05 02 (Shopify order number); the 940 Sales would send built as a dry run (control number 0; no sequence, no document, no outbox row); compared segment by segment after dropping trailing empty elements and masking ISA09/10/13, GS04/05/06, ST02, SE02, GE02, IEA02, W05 02, G62*10 and G62*38 02–04, N9*E1 02. Statuses: Match, Differs, Expected difference, No Sales order (none or several), Sales only (a shadow order that would qualify and no reference matched), Unreadable. "Mark as expected difference" (`sales.order.operate`, a reason, audited) holds while the set of differences (its signature) stays the same. Page Operations → Shadow compare (views, counts per status, both files side by side with the differing segments marked).
- Moved to P5 (user, 2026-11-02): matching real 997 / 945 / 944 against the reference 940s and writing "what UniOps would have recorded" into `sales_shadow_matches`. P5 must not apply a 945 / 944 with `shadow = true` to orders or write anything to Shopify for it.

### 5.5 Dev and test never reach partners

The dev server snapshot contains the real production SMTP credentials, and connection rows can be imported from production. So reaching a partner is gated by **environment switches that exist only in `.env.prod`**, independent of any connection's mode or any database setting:

| Action | Allowed only when | Otherwise |
| --- | --- | --- |
| Email to any address outside the company domain(s) (Radiant, retailers) | `SALES_EXTERNAL_EMAIL_ENABLED=true` | not sent; status **Suppressed**; logged in `sales_log` with recipients, subject and body hash ("would have sent") |
| SFTP `put` to a LIVE folder | `SALES_PARTNER_WRITES_ENABLED=true` **and** connection mode LIVE | not written; status **Suppressed**; logged as "would have written" |
| SFTP after-read Move / Delete on a partner server | `SALES_PARTNER_WRITES_ENABLED=true` | treated as Leave (the intake step catches `Suppressed`) |
| Any Shopify write (fulfillment, tracking, archive) | `SALES_PARTNER_WRITES_ENABLED=true` | not called; status **Suppressed**; logged |

**Suppressed** (product owner, 2026-10-28): a refused send or write is never reported as done — the outbox row and the outbound document end Suppressed (PRD 9.1). Turning a switch on does not send what was suppressed; a person reviews each one and resends or cancels it. Task Inbox mail follows the same switch: while `SALES_EXTERNAL_EMAIL_ENABLED` is false, tasks are written as already notified (§3.5).

Reads (SFTP list/get, Shopify GET) stay possible in dev when credentials are deliberately provided, so replay and connection tests work. The checks sit in the lowest-level clients (`email.send_external`, `sftp.put/move/delete`, `shopify.post/put`), so no flow can bypass them, and the health page shows both switches. Internal email to company addresses goes through the Task Inbox outbox as today.

## 6. API and permissions

Permissions (identity migration `0023_sales_perms`; module `sales`; front ends read them from epms-api `/config/me/permissions`):

| Key | Used by |
| --- | --- |
| `sales.read` | all GET endpoints, all pages |
| `sales.order.operate` | hold release, Send 940 now, delivery confirmation, manual orders, review resolution, record cancellation/return, cancel |
| `sales.file.retry` | resend / reparse / ignore documents, run now |
| `sales.transfer.manage` | create, send, cancel transfers; review receipts |
| `sales.inventory.adjust` | reserved for the next version (no endpoint in v1) |
| `sales.invoice.manage` | edit, send, resend invoices |
| `sales.config` | partners, items, DC routing, connections, settings, notifications, CSV import/export |

D21: at go-live the operator's role (`sales_operator`) holds all seven; adding people later is an Access Control Matrix assignment only.

Endpoint groups under `/api/v1` (all JSON; list endpoints paged with `page`, `page_size` ≤ 200, `sort` from a whitelist, `total`):

- `orders` (list, detail, lines, documents, timeline), `orders/{id}/send-940`, `/release-hold`, `/cancel`, `/record-return`, `/resolve-review`, `orders/manual` (Draft, submit)
- `worklists/review` (built in P3 as the Orders system view "Needs review", 2026-10-30), `worklists/delivery-confirmation` (+ bulk confirm), `worklists/action-required` (reads the shared tasks for `document_type = sales`)
- `shipments`, `invoices` (edit, send, resend, PDF), `transfers` (+ send, cancel), `receipts`
- `documents` (list, detail, raw download, reparse, resend, ignore)
- `runs` (list, detail, run now), `health`, `dashboard`
- `config/partners|items|item-units|partner-codes|dc-routing|warehouses|connections|settings|notification-rules` with `GET …/export.csv` and `POST …/import.csv` (validate-all-then-write, dry-run preview, rejects unknown columns; FR-OB-09)

## 7. Front end

`sales/` follows `inventory/` (session hand-off from the Portal hash, `@uniops/shell` tabs, TanStack Query, permissions from epms-api, branding). Unlike Inventory, **each nav item is gated by its permission and the landing page is the first visible item** (the Finance pattern), because operators and readers see different sets.

| Page | Content | Reached with |
| --- | --- | --- |
| Dashboard | order cards (FR-OB-52b), open-work counts | read |
| Operations Health | checks and cards (FR-OB-52a), connection latches, last runs, go-live gate | read |
| Orders | filterable list; order detail with lines, 940 history and locked values, shipments and lots, documents, timeline, actions | read (actions need order.operate) |
| Review | orders in Review with the reason and the fix (map item, confirm case pack) | order.operate |
| Delivery confirmation | worklist, bulk confirm with delivered quantities | order.operate |
| Action Required | Order Bridge tasks, one row per problem | read |
| Manual order | Draft → Submit with EA preview | order.operate |
| Invoices | list, edit, send / resend, PDFs | invoice.manage |
| Transfers | list, create, send, receipts and discrepancies | transfer.manage |
| Documents | register, raw view, reparse / resend / ignore | read (actions need file.retry) |
| Runs and logs | runs with pass reports, log search by order / file / date | read |
| Configuration | partners, items and units, partner codes, DC routing, warehouses, connections (secret present/missing, test), settings, notification rules; CSV import/export on each | read, read-only (§6: `sales.read` covers every page; product owner, 2026-10-29); changes, deactivation and import need config |

Reachability is part of done for every page: Portal card and sidebar entry visible with any of the seven keys; module nav shows only permitted pages; a user with none sees the "no access" screen; Task Inbox links (`HREF_MAP` / `taskHref` in epms and Portal) open the right Order Bridge page.

## 8. Notifications

- Event types are the FR-OB-56 list (plus 945 validation, paid order archived, transfer discrepancy). Each event maps to a task type `sales_<event>`; rules in `sales_notification_rules` choose TASK / EMAIL / BOTH and recipients (default: the operator, X2).
- Internal: tasks in the shared `tasks` table; the epms-api task notifier sends the email when the type's policy is on. New task types default to email OFF in `task_notify_policy.DEFAULT_IMMEDIATE`, so the go-live gate includes turning them on in Portal → Admin → Task Notification Policies.
- Registrations needed outside sales-api: `epms/src/lib/taskTypes.ts` (`TASK_TYPE_LABELS`, `HREF_MAP` / `taskHref` for `document_type = sales`), `epms/src/services/tasks.ts` (`TaskType`), `portal/src/pages/PortalHome.tsx` (labels, module mapping, heading), and `epms-api/app/crud/task.py` `_TASK_NO_STATUS_INVARIANT` (external types). The Portal deduplicates inbox rows by `document_number`, which is why problem keys are unique per problem.
- External emails (Radiant cancellation, retailer invoice) are sent by sales-api through an aiosmtplib helper with the X3 account. Every user-supplied value inserted into an HTML email is passed through `html.escape` (the `_render(…, escape=True)` pattern of epms-api); templates are not user-editable in v1.

## 9. Migration from Zoho

1. **Configuration:** export partners, items, DC routing (457 rows), warehouses and settings from Zoho as CSV; import through the configuration screens (dry run first). Fix the known data issues at import (CF0110 UPC, Jean Coutu invoice email, Overwaitea).
2. **History:** a one-off importer (`app/scripts/import_zoho.py`, run inside the sales-api container against an export folder) loads orders, lines, lots, transfers and the file register; raw files from WorkDrive are loaded into `sales_documents` with their sha256. Status values are mapped by the PRD 9.1 table. Orders shipped, completed or cancelled in Zoho, and Shopify orders below the cut-off, get `historical = true`.
3. **In flight:** orders without a 940 or waiting for 997/945 keep their mapped status; for Shopify orders the Zoho-created fulfillment id is stored (`shopify_fulfillment_id`, AC-OB-72).
4. **Counters:** `sales_order_number_seq`, `sales_x12_control_seq` and the transfer number start above Zoho's highest; the Shopify cursor is handed over at the switch.
5. **Checks:** row counts per table, totals per status, and the full-history dry run (AC-OB-21) before any send is enabled.

## 10. Testing

- **Where:** tests run only in throwaway containers on the dev network with a `*_test` database (the sales-api conftest refuses any database name not ending in `_test`, as inventory-api does) — never with the host `.env`, which points at production. Schema built by `alembic upgrade head` per test session.
- **Unit:** eligibility predicate (SQL and Python agree), status transitions, X12 builders and parsers, AK/945/944 matching rules, unit conversion, CSV import validation, problem-key dedup.
- **Replay:** archived Zoho/Radiant/TrueCommerce files (850, 940, 945, 997, 943/944, 810, Shopify pages) are fixtures; the two real samples in Appendix A.4/A.7 are fixtures too.
- **Integration:** SFTP against a local SFTP container; Shopify against a recorded-response stub; Task Inbox rows checked in the shared tasks table.
- **Shadow run:** the comparison job and the go-live gate (PRD section 11); shadow-mode matching of real 997/945/944 against Zoho reference documents (§5.4) is tested with replayed Zoho/Radiant pairs and must raise no review task.
- **Outbound safety (§5.5):** tests assert that with the two switches false (the default in every test and dev stack) an external email, a LIVE `put`, a Move/Delete and every Shopify write are refused and logged, and that they happen only with the switch true and (for SFTP) the connection LIVE. These are negative-and-positive pairs, so "everything refused" cannot pass by accident.
- **Outbox retry policy (§13.7):** a Shopify fulfillment write that times out is attempted exactly once, marked Failed and raises one task, and a manual retry that finds the fulfillment already present writes nothing; an external email whose SMTP connection is refused is retried up to the configured count; an email accepted by the server (2xx after DATA) is sent exactly once even if a later step fails; an SFTP put of a 940 that fails is attempted once and waits for Send 940 now.
- **Platform folders:** the one-way-dependency checks (back-end AST test, front-end ESLint/vitest rule) run in every test run and fail on any import of business code from `app/platform/` or `src/platform-ui/`.

| Acceptance criteria | Verified by |
| --- | --- |
| AC-OB-01 … 09 (intake) | integration tests with the SFTP container and crafted files |
| AC-OB-10, 11 (Shopify, 850 multi-PO) | stubbed Shopify; replay |
| AC-OB-12 … 20, 70, 74, 79 (matching, units, review, source changes, manual, payment, delay clock) | unit + replay |
| AC-OB-21 (full-history dry run) | migration import + dry run on a copy of production data; a person agrees the eligible set |
| AC-OB-22 … 30, 63 (eligibility, archive, Send 940 now, 940 content, control numbers, validation) | unit; AC-OB-25/26/27/28 by segment-by-segment replay against archived Zoho 940s |
| AC-OB-31 … 33, 62 (997, timeout, Accepted by 945) | unit + replay |
| AC-OB-34 … 39, 56 … 61, 72, 73, 77, 78 (945, validation, write-back) | replay of archived 945s; Shopify stub |
| AC-OB-40 … 45 (cancellation, delivery, invoices) | unit; AC-OB-44 replay against archived 810s |
| AC-OB-46 … 55, 71 (operations, notifications, audit, go-live gate) | integration (two cycles, killed run, auth latch, tasks); front-end reachability check with the headless browser |
| AC-OB-64 … 69, 75, 76 (transfers) | unit + the real 943/944 pair |

## 11. Implementation plan

Sizes are working days for one developer, including tests. Each phase ends with its tests green in the throwaway container and a short demo on the dev stack.

| Phase | Content | Done when | Size |
| --- | --- | --- | --- |
| P0a Platform, UI library, template | `tools/new-module`, `tools/check-scope.sh`; the bare `sales-api` and `sales` skeletons generated by `tools/new-module --platform=inplace` (product owner, 2026-10-27), registered nowhere — no compose, Caddy, Portal, identity or CORS change; `sales-api/app/platform` (§13.1–13.14) and `sales/src/platform-ui` (§13.15) written into them, each with README, tests and the one-way-dependency check; the four 9.1 machines in `sales-api/app/domain/status.py` | platform unit tests green (state machine vs 9.1, exclude_unset clear/untouched, CSV validate-all, guard decorator negative + positive, outbox retry policy); UI library builds and typechecks in sales · scope check + regression gate pass | 7–9 |
| P0 Wiring | wiring of the P0a skeletons from the template's printed checklist: compose/Caddy/env/CORS/Portal, identity permissions migration, health, `sales01`, guide-layer module entry | module reachable from the Portal with the seven keys; health green; guide test passes · scope check + regression gate pass | 2 |
| P1 Configuration | config entities on `CrudService` / `CrudPage`, CSV import/export, connections with secret status and connection test (started 2026-10-29 with one vertical slice, **Partners**, and `sales02_audit_outbox`; the other tables follow on the same pattern) | full Zoho configuration imported in dry run and for real on dev · scope check + regression gate pass | 2 |
| P2 Scheduler and intake | `sales03`, scheduled jobs (cycle, outbox), SFTP gateway, intake pipeline, documents page, runs/logs (built 2026-10-29 with the Connections page brought forward from P1 — intake needs connection rows — plus `host_key`, identity rows and the fail-closed host list; Shopify fetch is P3; reparse / resend / ignore on the documents page arrive with the phases that parse and send) | AC-OB-01…09, 46…48 pass against the SFTP container · scope check + regression gate pass | 5 |
| P3 Shopify inbound | Shopify gateway, order upsert, units, payment hold, delay clock, archive rules, review worklist, manual orders; the **Shopify connection test** (a read-only shop-info call through the guarded Shopify gateway — until then the test answers "arrives with the Shopify gateway (P3)" and never touches SFTP; 2026-10-29) | AC-OB-10, 12…20, 22, 23, 70, 74, 79 · scope check + regression gate pass | 5 **As built (2026-10-29):** brought forward from P4 — retailer 850 parse and upsert, with Walmart DC routing → review (C1); the Item Master CRUD — items with their units, partner codes, DC routing (C4); the Shopify channel settings on the connection row (C5). Delivered for eligibility: every input on the order and the pure `qualifies()` with AC-OB-17/22/23/74 as tests (C2). Moved to P4 (need a 940): AC-OB-14's rendered `W01`, AC-OB-18 and AC-OB-70's "refunded after the 940" (locked values, before/after task), the SQL twin of `qualifies()` and the queueing (C3). Status additions Pending → Review, Payment Hold → Review, Review → Payment Hold (FR-OB-12/15/16; PRD 9.1 note). Units from a Shopify variant title (product owner, 2026-10-30, after the real dev2 pull): the title decides — a unit's name (not EA's) or one of its `shopify_titles` contained in the title (compared as lower-case letters and digits, longest first) gives that unit; any other title is EA; safety net `looks_like_pack()` — a number ≥ 2 and a pack word (case, pack, box, can/cans, ct, x) with no unit matched — sends the line to review. Applies to Shopify lines only: an 850 line takes its UOM and one without a UOM goes to review (it used to fall through to the title rule and become EA). Shopify item matching (product owner, 2026-10-30): SKU → variant barcode as GTIN → as UPC → review, no item-code fallback; the barcode comes from the gateway's read-only `variant()` (`BarcodeLookup`, cached per run / per re-check; the re-check uses the order's partner's active SHOPIFY connection), stored on the line's `source`; a failed read → review with the reason; `barcode_key()` = digits only, zero-padded to GTIN-14 (8–14 digits, else no barcode); a barcode on two items → review. The titles are written after the unit in the Item Master's units text and CSV: `EA=1; CASE=6 [Case (6 Cans)]; 2PACK=2 [2-Can, 2-Pack]`; one title may name only one unit of an item. |
| P4 940 / 997 | eligibility, builders, validator, control sequence, sending, 997 parsing, timeout, Send 940 now, 850 inbound | AC-OB-11, 21 (on dev data), 24…33, 62, 63; segment-by-segment replay of archived 940s · scope check + regression gate pass | 6 (850 inbound moved to P3, 2026-10-29; P4 adds what P3 left for it — see the P3 row) **As built (2026-11-02):** eligibility = `qualifies()` plus its SQL twin `eligible_clause()` (a randomized parity test); the cycle queues due orders (re-checked under lock), then the outbox puts, then 997s and timeouts — the scheduler stays off, Run now and Send 940 now only. The 940 is built per PRD Appendix A (`app/domain/edi940.py`; X12 helpers in `app/domain/x12.py`) and checked before it is queued (FR-OB-29a): a failure keeps the file as Cancelled, the order 940 Failed and raises a task, nothing is sent. The control number comes from `sales_x12_control_seq` (a new one on every resend). Sending is an outbox kind (ONCE; `pre_check` re-reads document and order under lock): TEST connection → `test_outbound_folder`, LIVE → `outbound_folder`; Sent sets `sent_at` and the order 940 Sent, never Accepted. Writes need a third fail-closed guard, `SALES_SFTP_WRITE_HOSTS` (empty = no writes; a host allowed for reading but not for writing is refused before any traffic; dev lists only `sftp-test`; production lists the warehouse at release, DEPLOYMENT.md §4a). 997s are matched by AK1 group / AK2 set control (leading zeros ignored) to an outbound 940 at RADIANT; zero or several matches → a task; rejected → 940 Rejected with the AK codes and a task, never resent by itself. No 997 within `edi.ack_timeout_minutes` (default 120) of `sent_at` → Timeout and a task naming the partner and the Toronto send time; a late 997 still applies and closes it; a 945 first → Accepted by 945, no timeout. Send 940 now (`POST /orders/{id}/send-940`, `sales.order.operate`): Pending (no delay, any sending mode), Rejected / Timeout / Failed; a live 940 only with `replace=true` after a confirmation (old one Replaced, `replaced_by_id`; status additions 940 Sent / Accepted → 940 Generated, PRD 9.1 note). A source change after the 940 updates the order and raises one before/after task against the 940's locked values (AC-OB-18, -70). Dry run `GET /orders/eligibility-preview` (AC-OB-21; Orders → Next 940s). Settings `edi.warehouse_connection_id` (unset: no 940, fail closed) and `edi.ack_timeout_minutes` on `GET/PUT /config/edi-settings` (`sales.config`; only these two — the scheduler's settings are not exposed) and Connections → Warehouse EDI. Order detail: Warehouse 940s with file and 997 status side by side. Open: the Appendix A assumptions (N1 qualifier 94, GLN as UL, N9 E1 `CRM-940_<9-digit>`, line N9 ZZ/UP/BP) and AC-OB-25's replay wait for real 940 / 997 files and the Zoho script; ISA15 stays `P` as in Zoho (§12 item 9). |
| Shadow run (pulled before P5, 2026-11-02) | shadow setting, Leave, flags, reference import, 940 comparison page (§5.4 As built) | tests + scope check + regression gate pass; dev2 shadow Run now | 2.5 |
| P5 945 and Shopify write-back | 945 parser, shadow matching of 997 / 945 / 944 against the Zoho references (moved from the shadow run, 2026-11-02), shipments and lots, short shipment, second 945, validation checks, fulfillment + tracking + archive, migrated fulfillments | AC-OB-34…39, 56…61, 72, 73, 77, 78 · scope check + regression gate pass | 5 **As built (2026-11-02):** `app/domain/edi945.py` parses every W12 under every LX (SSCC from `MAN*GM` as text; lot `N9*LT`, else W12-09; expiry `DTM*036`; weight W12-10; tracking `N9*2I` per shipment or per pallet; `N9*MB` / `N9*BM`; W27 mode / carrier; ship date from header DTM — 011 preferred — else G62) — **verified against Radiant's real 945s on 2026-11-03** (module docstring; anonymised fixtures in tests/fixtures): the expiry is `G62*36` (19000101 = none) and the ship date `G62*11`, tracking `N9*2I` follows each W12 (per parcel), an LX is a pallet (not the 940 line), the header carries `N9*PO` and Zoho's reference `N9*E1`. Radiant's 997s are group-level (AK1 + AK9, no AK2): the 940 / 943 is found by GS06 and decided by AK9. TrueCommerce's real 850s list lines under `Item` with nested `ShipTo` / `BillTo` and UOM `Case`; an order with no lines goes to Review. Several lines of one item share what a W12 shipped up to each line's quantity before anything counts as over-shipped. In the shadow run the missing Sales 940 is not a check, and a 945 whose W06-02 is Zoho's own number is matched by its PO (W06-06 / `N9*PO`) when one order has it. Order key (FR-OB-28): W06-02 else W06-06; numeric keys zero-normalised with exactly one trailing letter stripped, looked up by Shopify order id → internal number → source number (the first tier that finds any decides; several → review); two letters → review; a non-numeric key (a PO) exactly. `app/services/shipments.py`: a 945 is planned (order, line per W12 by item / UPC / buyer part with the 940's LX as tie-break, totals, short, the FR-OB-25d checks) then applied — shipment (document, set index) + rows; lines' `shipped_ea`; Sent / Accepted / Timeout → Shipped; Accepted by 945 when no 997; short → task; second different 945 → stored `applied = false`, order On Hold, task; no order → stored with its rows, task ("create order" action not built — narrow, user 2026-11-02); unmatched W12 → row `needs_review` + task. Checks are recorded on the shipment and logged as "945 validation" (P8 routes them). Ship-to compared by postal code with the 940's locked ship-to; the partner part of check (5) is not checked (no field in the 945 without a real sample). Shopify write-back (outbox kinds `shopify_fulfill` → `shopify_close`, ONCE): the effect always reads the order's fulfillments first (a retry never duplicates), adds the tracking to an open tracking-less fulfillment (Zoho's, AC-OB-72), else fulfills the open fulfillment orders with the shipped quantities in Shopify units (EA ÷ the line's factor) and the tracking (company Purolator, FR-OB-11; the first tracking number); then closes the order (needs `write_orders`, not granted: fails once with a task naming the scope). Writes need the switch AND a LIVE Shopify connection (guard `allow`). `pre_check` cancels during the shadow run or for a shadow shipment — never written, also after the switch-off; a person's Retry (`POST /orders/shipments/{id}/shopify-retry`, `sales.order.operate`) is refused for those. Shadow run: a 945 / 997 is planned, stored (945: shipment `shadow = true`, `applied = false`) and matched to the Zoho reference 940 (997 by group / set control; 945 by W05 03 = W06-06, else W05 02 ≈ W06-02), with the plan in `sales_shadow_matches.would`; statuses Match / No Sales order / No reference. Store check from dev2 (read-only, 2026-11-02): orders are fulfilled from the merchant-managed location "1680 Venture Dr" (fulfillment orders `request_status` unsubmitted; Zoho's fulfillments `service = manual`, no tracking) — so `write_merchant_managed_fulfillment_orders` suffices; `locations.json` is refused (no `read_locations`, not needed). 944 stays in P7. |
| P6 Invoicing | delivery-confirmation worklist, invoice senders (810 JSON, email with PDFs, none), invoice edit, cancellation/return | AC-OB-40…45 · scope check + regression gate pass | 5 **As built (2026-11-02; cut (b): AC-OB-40's email moves to P8 with the notifications, the task is here).** Invoice status lives on the order (9.1, with the Blocked hold). Each cycle `advance` moves a Shipped, non-shadow order Pending → Awaiting Requested Date → Waiting for Delivery Confirmation when its requested delivery date is reached (dc-routing partners: do-not-deliver-after; no date → at once); an applied 945 row that matches no order line (FR-OB-25 lot exception) holds it with a task. Confirmation (`sales.invoice.manage`, one or in bulk; delivered ≤ shipped, default shipped; not in the future) records the date (= invoice date) and `order_lines.delivered_qty`, builds `IN-<order number>` — cases = delivered EA ÷ the item's CASE factor, truncated (A.9), price per case = 850 unit price × CASE factor ÷ order-unit factor — and makes it Ready; a part case, no CASE unit or no price holds it (Blocked, task) — never invoiced silently short; Release (with a reason) returns it to Ready. Edit (FR-OB-35) of quantities, prices and date in Ready / Failed / Sent / Blocked, audited. Send / Send again is explicit (an edit window): outbox `send_invoice` (ONCE) — 810 JSON (`app/domain/invoicing.py`; line keys mirror the 850's — assumptions until AC-OB-44's replay) stored as a document and put to the "810 invoice connection" (setting `invoice.connection_id`) through the guarded SFTP path; file `810-<abbrev>-<internal number>-<PO>-<yyyyMMdd_HHmmss>.json` (A.10 has no 810 rule); or email (`SmtpEmail`, aiosmtplib) to the partner's invoice address with `invoice_cc` in copy, subject per A.12, HTML escaped, three PDFs (invoice, PO, packing list — `app/services/invoice_pdf.py`, ReportLab, CJK runs in embedded WenQuanYi Zen Hei). Refused by the guard → Failed with "not sent: …", logged, no task (the would-have-sent row keeps recipients, subject, body hash); a failure → Failed + task, never retried by itself. Shadow: `advance` does nothing while it is on; confirm and send are refused while it is on and for a shadow order (also after the switch-off); the outbox pre_check cancels (second barrier). FR-OB-19: record Returned / Cancelled after shipment with EA per line and reason → the invoice holds (Blocked) + task. FR-OB-18 / AC-OB-40: cancelled at source after the 940 → On Hold + one task stating what shipped ("nothing shipped yet" vs "N EA shipped"); a 940 not yet sent, rejected or failed → Cancelled. UI: Invoicing → Delivery confirmation (the orders list fixed to Waiting, bulk "confirm all shown", per-line delivered EA), Invoicing → Invoices (edit, send, release), the order's "Record a return or cancellation", the 810 connection on Connections. |
| P7 Transfers | 943 builder, 944 parser, allocation, discrepancies, transfer pages | AC-OB-64…69, 75, 76 · scope check + regression gate pass | 3 **As built (2026-11-02).** Warehouses got the 943's party fields and a Configuration → Warehouses page (CrudPage, CSV, `sales.config`; FR-OB-27). `app/domain/edi943.py` builds A.4 (the replay test against Zoho's real 00104 file differs only in the W06 date and the file name inside NTE); `app/domain/edi944.py` parses W17-04 / G62 / LX / W07 / G69 / N9*LT / W14 and allocates per A.7 (LX = line number → the line the lot already went to → the first line of the item with room), summing per line and lot, never overwriting; Received / Partially Received / Discrepancy (over-received, not on the transfer, unrecognised, W14 ≠ the W07 total). `app/services/transfers.py`: Draft (create / edit, `sales.transfer.manage`) → Ready To Send → the cycle builds the 943 (shared control sequence, 5-digit transfer number from `sales_transfer_number_seq`, file `CRM-943_<control>_TRID_<no>_.EDI`) and queues ONE put (outbox `send_943`, the three write locks; pre_check cancels during the shadow run); failed checks or a failed / refused put → Failed (new edge 943 Generated → Failed, PRD 9.1 note) with a task (a refusal by the guard: logged, no task); Resend gives a new control number. The 997 step routes AK2 943 here: accepted → Waiting for 944; rejected → Failed + task with AK5, never resent; the ACK timeout raises a task, the transfer waits. The 944 step: by W17-04; the first applies (lines' received EA and status, the transfer's outcome; a 944 before the 997 counts as the acceptance); a second, different one → stored, not applied, On Hold + task; no transfer → stored + task; Partially Received is logged (P8 notifies); Discrepancy raises a task listing the lines (AC-OB-76 on the real 16468). Shadow: no 943; a 944 is stored (receipt shadow, not applied) and matched to the Zoho reference 943 (W06-02 = W17-04) in `sales_shadow_matches` with the allocation it would have made. Cancel: Draft, Ready To Send, 943 Generated (the queued row and file are cancelled) or Failed only. UI: Transfers (ListPage; detail with lines and each 944's rows in LocalTable; a Draft edited in EditableTable; Ready to send / Resend 943 / Cancel with a reason). |
| P8 Health, dashboard, notifications | health checks and gate, dashboard, notification rules, task types registered in epms/Portal (**moved from P0**, product owner 2026-10-29 — no `sales_*` type existed at P0: epms `taskTypes.ts` labels and `taskHref` for `document_type = sales`, `services/tasks.ts` `TaskType`, epms-api `crud/task.py` `_TASK_NO_STATUS_INVARIANT`, Portal `PortalHome.tsx` task labels / module mapping / heading, and the epms Sales handoff in `epms/src/lib/api.ts` with `VITE_SALES_URL` for epms-frontend / epms-web — the last two are not in §14.2 yet and are added to it then) | AC-OB-49…55, 71; reachability check · scope check + regression gate pass | 3 **As built (2026-11-02).** Notifications: `app/domain/notifications.py` is the event catalog (32 events + the rules' test; `action` events always raise their task — FR-OB-06 — `notify` ones, 945 validation and partial receipt, follow the channel; seven are the go-live gate). Every raised problem goes through the rules in the tasks service (`app/services/problems.py` → `app/services/notifications.py`): the task goes to the rule's first active user, else the operator; an email (outbox `notify_email`, retried while not accepted) goes to every user's address and the external ones in ONE message, once per problem (a new task, never a refresh); no rule → the task to the operator and no email (AC-OB-52); plain text only. D-b holds: switch off → SUPPRESSED + the "would have sent" row; tasks keep `notified_at = now`. The shadow gate runs first (order work: no task, no email). Newly notified: the 945 validation checks (one per shipment, every check in it), Partially Received, and every send the guard refused (`send_refused`, one per outbox row; 940, 943, invoice, Shopify). History: `sales_notifications` (sales13; an email's delivery = its outbox row). Configuration → Notifications (CrudPage; users as a multiselect, other addresses one per line; "Send test notification" records `tested_at` / `test_result`); Operations → Notification history. Gate: a connection goes TEST → LIVE only when every gate event has an active rule with recipients and a sent test (`connection.go_live_gate`). Health (`GET /operations/health`, FR-OB-52a): required settings, each connection (auth latch / inactive warehouse connection FAILED; "Test" = the connection test, reads only), the scheduler (more than one RUNNING / stuck FAILED; paused WARNING, FAILED after `scheduler.paused_alarm_minutes`; off in this environment or no success in 3 intervals WARNING), failed files, rejected / timed-out ACKs, 945 / 944 exceptions, transfer discrepancies, the outbox (failed, queued longer than `health.outbox_backlog_minutes`, suppressed), open tasks older than `health.task_age_hours`; the switches and the gate. Dashboard (`GET /operations/dashboard`, Home, FR-OB-52b): the ten cards, each the total of the list it opens (orders' `order_state` is now filterable in SQL — `ORDER_STATE_SQL`, the same rule as `order_rules.order_state`), what needs a person (review, delivery confirmation, open tasks by type), the last runs and the health. Task Inbox: every Sales task links to the Sales page `/inbox/<problem key>`, which asks `GET /inbox/{key}` for the page and record and opens it (`requestOpen`; a record not on the list's first page is fetched by id). Registered: epms `taskTypes.ts` (labels, `taskHref` for `sales`, `document_number` on `TaskHrefInput`), `services/tasks.ts` (`sales_${string}`), `lib/api.ts` (`SALES_URL`, `salesHandoffHref`), `epms/Dockerfile` + compose (`VITE_SALES_URL` for epms-frontend / epms-web); epms-api `_TASK_NO_STATUS_INVARIANT` (so the types appear in Portal → Admin → Task Notification Policies, email off by default); Portal `PortalHome.tsx` (Sales heading, labels, module mapping — two listed line edits). |
| P9 Zoho migration | importer, status map, historical flag, counters, in-flight fulfillments, row-count checks | full import on a copy; AC-OB-21 dry run agreed by a person · scope check + regression gate pass | 4 |
| P10 Shadow run and cutover | comparison job, test folders, channel switches, runbook | PRD section 11 exit criteria met; per-channel switch · scope check + regression gate pass | 2 build + calendar time |

Total ≈ 49–51 build days (P0a at 7–9), plus the shadow-run calendar period.

**Guide layer:** P0 adds an `sales` entry to `epms-api/app/knowledge/modules.yaml` (key, label, covers, who, typical, notes — prose only, no `documents`/`reports`) and to the presence check in `tests/test_module_guide.py`; every later phase that changes what a user can do updates that entry in the same change (feedback rule). Status names and rules are not copied into the YAML; if the assistant must answer them later, they are derived from the sales-api status module.

## 12. Risks and open points

**Open PRD items (unchanged):** OB-OI-02 (inventory recalculation history — next version), OB-OI-04 (Zoho completed count of 8,000 — affects the migration row-count check), OB-OI-07 (who releases paid Shopify orders today — affects the shadow-run comparison of held orders), OB-OI-30 (credit note and inventory handling for returns — FR-OB-19 records only), OB-OI-38 (Radiant data completion — next version).

**Found while designing (need an answer, not invented here):**

1. **X1–X4** — accepted 2026-10-23 (§1; X1 recorded in the PRD as D22). DNS records for the two host names are arranged by the user.
2. **Shadow-run file access:** Zoho reads Radiant and TrueCommerce through ftp-api.com; if Zoho's `*_DELETE_AFTER_DOWNLOAD` is on, or Radiant moves files after Zoho reads them, UniOps will not see the same files. Confirm both settings before P10.
3. **Radiant test folders and shadow-mode acknowledgements (§5.4):** confirm with Radiant whether `/test/in` is processed, whether it returns 997s and 945s in `/test/out`, and that it never ships. Without test-side responses, PRD section 11 step 2 (a) is met only through the Zoho reference matching, and the first real UniOps round trip happens at the channel switch. Also confirm how Zoho's sent 940/943 can be read continuously (Radiant inbound folder with Leave, or a recurring export of the Zoho file register).
4. **Dev reaching partners (§5.5):** the dev snapshot holds real production SMTP credentials; the two prod-only switches must stay false outside `.env.prod`, and the release checklist verifies them.
5. **Shopify custom app:** the client-credentials app exists (2026-10-29); its client id / secret are in the dev2 environment. Its scopes (§5.2) are confirmed in P3.
6. **Task notification default OFF:** new task types send no email until enabled in Task Notification Policies — part of the go-live gate, easy to miss.
7. **Zoho export completeness:** the importer needs the export of lots, file register and WorkDrive files with their original bytes; confirm the export path before P9.
8. **Shopify scopes — add `write_orders` to the app before P5** (checked from dev2, 2026-10-29, read-only, store 561df5-3.myshopify.com: client-credentials grant OK, `expires_in` 86399; granted `read_all_orders`, `read_orders`, `read_products`, `write_merchant_managed_fulfillment_orders`). Archiving an order after fulfillment (FR-OB-11a: REST `orders/{id}/close`, GraphQL `orderClose`) needs `write_orders`, which is not granted. Fulfillment and tracking (FR-OB-11, D16) go through fulfillment orders: `write_merchant_managed_fulfillment_orders` (granted) covers orders at a merchant-managed location; if Radiant is set up in Shopify as a third-party fulfillment-service location, `write_third_party_fulfillment_orders` is needed instead (not granted) — confirm which in P5 before the first write. §5.2's `read/write_fulfillments` are the older scopes these replace. `read_all_orders` (orders older than 60 days) is granted, which the P9 history import needs.
9. **ISA15 for the warehouse test folder (P4, 2026-11-02):** UniOps sends ISA15 = `P` in both modes, exactly as Zoho. Ask Radiant whether files put in `/test/in` should carry `T` instead; changing it is one constant in `app/domain/edi940.py`.

## 13. Platform foundation (Order Bridge only)

Approved 2026-10-23: an ABP-inspired foundation in the back end and a UI library in the front end, **used only by Order Bridge first** (isolation rules in §14). Product owner, 2026-10-27: keep both inside this project for now, each in its own folder so it is easy to separate later.

| Part | Folder | Used by |
| --- | --- | --- |
| Back-end foundation | `sales-api/app/platform/` | `sales-api/app/services/…`, `app/domain/…`, `app/integrations/…`, `app/api/…` |
| UI library | `sales/src/platform-ui/` | `sales/src/pages/…`, `src/features/…` |

**Layers (STEP 1, product owner 2026-10-29 — a restructure, no new capability).** Back end, the UniOps layout:

| Folder | Holds | May import |
| --- | --- | --- |
| `app/api` | thin HTTP endpoints; a write endpoint commits exactly once, at the end | everything below |
| `app/services` | application services (use cases); each declares and checks its permission (§13.5) | schemas, domain, models, integrations, platform |
| `app/schemas` | DTOs (pydantic): shape only — types, lengths, required fields; no business rule | platform |
| `app/domain` | business rules, the 9.1 status machines | platform |
| `app/models` | SQLAlchemy models (`sales_*`) | platform |
| `app/integrations` | outside systems: `sftp`, `email` (protocols, fakes, guarded/read lists), `x12/`, `epms_tasks` (the shared Task Inbox table) | platform only — never domain, services, api |
| `app/platform` | generic bases (entities, values, query, repository, uow, events, outbox, audit, state, services bases, crud_router, registry, guard, pipeline, jobs, settings_registry, errors, actors, authorization, container helpers) | nothing else of `app` |

Front end: `src/platform-ui` in three layers (§13.15), `src/features/<area>/` (orders, fulfillment, invoices, transfers, configuration) and `src/pages` built on `@/platform-ui` only.

**Extraction-ready rules (both folders):**

- **One-way dependency:** the platform folder never imports business code. Back end: a test walks the AST of every module under `app/platform/` and fails on any import of `app.` outside `app.platform` (import-linter style, no new dependency); a second walk fails on any import of `app.domain`, `app.services` or `app.api` from `app/integrations/`; each rule has a positive half and a planted-import proof. Front end: ESLint `no-restricted-imports` rules per layer (business code outside `@/platform-ui`; primitives → patterns/resource/`@tanstack/react-query`; resource → primitives/patterns/`@uniops/shell`; patterns → `@uniops/shell`; `@uniops/shell` anywhere in Sales except `platform-ui/primitives` and the app shell), and a vitest test that resolves every relative import against the same rules; `tools/check-scope.sh` plants a violation for each ESLint rule.
- **No Order Bridge names** inside the platform code: no partner, order, 940 or Radiant identifiers. Domain enums, status maps and permission keys are passed in by the business code (e.g. `StatusBadge` takes a map; `useCan` takes a key).
- **Own README and tests:** `app/platform/README.md` and `src/platform-ui/README.md` describe the public interface; tests live next to them (`tests/platform/…`, `src/platform-ui/**/*.test.tsx`).
- **Own dependency list documented:** each README lists the third-party packages the folder needs (back end: SQLAlchemy, pydantic, asyncssh, httpx, aiosmtplib; UI: Radix, `@tanstack/react-table`, `react-hook-form`, `zod`), so extraction knows exactly what moves.
- **Extraction later** = move the folder, add its own package manifest, change imports. No code inside it changes.

**Platform tables (interpretation agreed 2026-10-27):** the tables the platform works with — audit, outbox, settings, runs, log, X12 control sequence — are named `sales_audit`, `sales_outbox`, `sales_settings`, `sales_runs`, `sales_log`, `sales_x12_control_seq` in this document, but no such name appears in `app/platform/`. The platform ships the column shapes as mixins and factories; `sales-api/app/models` supplies the table names (and the sequence name) when it declares the concrete models, and the tables are created by `sales01` (P0: settings), `sales02` (P1: audit, outbox), `sales03` (P2: runs, log) and `sales04` (P4: the X12 control sequence). Platform tests declare their own test tables (`create_all` in the `sales_test` database).

### 13.1 Entity base classes

Mixins on the SQLAlchemy declarative base; inheritance never deeper than three levels.

```python
class Entity(Base):                      # id, created_at
    __abstract__ = True
    id: Mapped[int] = mapped_column(primary_key=True)

class CreationAudited(Entity):           # + created_by (user id or SYSTEM_ACTOR)
    __abstract__ = True

class Audited(CreationAudited):          # + updated_at, updated_by
    __abstract__ = True

class Versioned:                         # mixin: optimistic locking
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    __mapper_args__ = {"version_id_col": version}

class SoftDeletable:                     # mixin: available, not used in v1
    deleted_at: Mapped[datetime | None]
    deleted_by: Mapped[int | None]

class Deactivatable:                     # mixin: config rows referenced by orders
    active: Mapped[bool] = mapped_column(default=True)
```

- Config tables referenced by orders (`sales_partners`, `sales_items`, `sales_item_units`, `sales_dc_routing`, `sales_warehouses`, `sales_connections`) are `Audited + Versioned + Deactivatable` and have **no delete** in the repository or the API.
- Documents (`sales_documents`) are `CreationAudited` only and have **no delete and no update of content** (FR-OB-05); status columns change only through the state machine.
- `SoftDeletable` is **available, not used in v1**: the platform provides the mixin with its automatic filter (§13.3) and helpers for partial unique indexes (`unique … WHERE deleted_at IS NULL`); each entity opts in. No Order Bridge entity uses it in v1 — drafts follow PRD 9.1 (Draft → Cancelled), config tables use `active`, documents are never deleted.

Example config entity:

```python
class Partner(Audited, Versioned, Deactivatable):
    __tablename__ = "sales_partners"
    code: Mapped[str] = mapped_column(unique=True)        # immutable after first document
    name: Mapped[str]
    settlement_mode: Mapped[SettlementMode]               # PREPAID | INVOICE
    sending_mode: Mapped[SendingMode]                     # AUTOMATIC | MANUAL
    delay_minutes: Mapped[int]
    invoice_method: Mapped[InvoiceMethod]                 # 810_JSON | EMAIL | NONE
    invoice_email: Mapped[str | None]
    min_shelf_life_days: Mapped[int | None]
```

### 13.2 Value objects

```python
@dataclass(frozen=True)
class Quantity:
    amount: Decimal
    unit: str                                # EA, CASE, 2PACK, …
    def to_ea(self, conversions: UnitTable) -> "Quantity":
        """Raises MissingConversion when the item has no factor for `unit`,
        FractionalQuantity when the result is not a whole EA (both are
        ConversionError). Never defaults to 1 and never rounds: Radiant ships
        EA exactly as told (PRD FR-OB-02/14). The caller turns ConversionError
        into Review + task (FR-OB-15; fractional EA: product owner, 2026-10-28)."""

@dataclass(frozen=True)
class Money:
    amount: Decimal                          # full precision (unit prices keep 5+ decimals, as in NC)
    currency: str                            # CAD | USD
    def __add__(self, other: "Money") -> "Money": ...   # refuses mixed currencies
    def rounded(self) -> "Money": ...        # half-up to the minor unit

def line_total(unit_price: Money, qty) -> Money: ...   # price × qty, rounded half-up once
def total(lines: list[Money], currency: str) -> Money: ...   # invoice total, rounded
```

Nothing rounds implicitly (product owner, 2026-10-28): line totals and invoice totals round half-up to the minor unit through `line_total` / `total`. A `float` is refused (TypeError) in `Money`, `Quantity` and as a `*` operand — it has already lost the value.

### 13.3 Repository and query specification

```python
class QuerySpec(BaseModel):
    page: conint(ge=1, le=10_000) = 1        # no deep offsets
    page_size: conint(le=200) = 50
    sort: list[SortKey] = []                 # multi-column; each key whitelisted
    search: constr(max_length=100) | None = None   # over the resource's declared search fields
    filters: list[Filter] = []               # ≤ 20; field + op (eq | in | contains | range), whitelisted;
                                             # an `in` list ≤ 100 values, each value ≤ 200 characters
    include_historical: bool = False

class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int                               # the UniOps list contract {items, total}

class Repository(Generic[T]):
    model: type[T]
    search_fields: tuple[str, ...]
    sortable: Mapping[str, ColumnElement]    # only these keys are accepted
    filterable: Mapping[str, FilterRule]
    def __init__(self, uow: UnitOfWork): ...
    async def get(self, id: int, *, for_update: bool = False) -> T: ...
    async def list(self, spec: QuerySpec) -> Page[T]: ...
    async def add(self, entity: T) -> T: ...
    def base_query(self) -> Select: ...      # applies automatic filters
```

Automatic filters in `base_query`: soft-deleted rows are excluded for entities that opt in to `SoftDeletable` (none in v1); on **open-work lists** (dashboard cards, worklists, Warehouse Waiting, Invoice Work) historical orders are excluded (FR-OB-52b); elsewhere they are included only with `include_historical=true`. Unknown sort or filter keys are a `BusinessError("query.unknown_field")`, never passed to SQL. A filter value is converted before it reaches SQL — by the rule's `cast`, or by default from the column's Python type — so a value that does not convert is a 422 (`query.bad_value`), never a database error.

### 13.4 Unit of work

```python
class UnitOfWork(Protocol):
    session: AsyncSession
    events: EventBuffer                      # §13.7
    async def __aenter__(self) -> "UnitOfWork": ...
    async def commit(self) -> None: ...      # dispatch in-transaction handlers, then COMMIT
    async def __aexit__(self, *exc) -> None: ...   # rollback on error
```

One unit of work per HTTP request (FastAPI dependency `get_uow`) and one per scheduler unit (one connection, one document, one order — §4.2). Nothing is committed unless `commit()` is called — unlike the skeleton's `get_session`, which commits at the end of every request; `get_uow` never goes through it (tested).

**Transaction contract — one transaction per operation (STEP 1, 2026-10-29).**

- Only the **outermost caller** commits: the HTTP endpoint — "an endpoint that writes calls `uow.commit()` exactly once, at the end" — or a job's unit (`isolated`, §13.11).
- Services, `CommandService.execute` and the step functions a job runs **never commit**; they flush at most. A service that calls another passes its unit of work along, so the whole operation is one transaction: if the second service fails, nothing of the first is kept — no row, no audit row, no outbox row (tested, both orders).
- The one exception is the outbox worker (§13.7), which commits its claim (IN_FLIGHT) and the outcome in two transactions of its own, for at-most-once.
- A static test walks every `post/put/patch/delete` endpoint (`app/api/**`, `crud_router`) and fails unless it contains exactly one `.commit()`.

### 13.5 Services (template method)

```python
class ReadOnlyService(Generic[T, Read]):
    repo: Repository[T]
    read_schema: type[Read]
    permission: str = "sales.read"
    async def get(self, id: int) -> Read: ...
    async def list(self, spec: QuerySpec) -> Page[Read]: ...

class CrudService(ReadOnlyService[T, Read], Generic[T, Read, Create, Update]):
    permission: str = "sales.config"  # every config CRUD uses the coarse key
    async def create(self, data: Create) -> Read: ...
    async def update(self, id: int, data: Update, version: int) -> Read: ...
    async def deactivate(self, id: int) -> Read: ...
    # hooks
    async def validate_create(self, data: Create) -> None: ...
    async def before_update(self, entity: T, changes: dict) -> None: ...
    async def after_delete(self, entity: T) -> None: ...          # only for SoftDeletable entities (none in v1)
    # CSV
    async def export_csv(self, spec: QuerySpec) -> bytes: ...
    async def import_csv(self, file: bytes, *, dry_run: bool) -> ImportReport: ...
```

- `update` applies `data.model_dump(exclude_unset=True)`: a field sent as `null` is cleared, an absent field is untouched. A dedicated test sends `{"invoice_email": null}` and asserts it is cleared, and sends `{}` and asserts nothing changed (the PA tax-clearing lesson).
- `import_csv` parses and validates **every** row first; any error returns the full error list and writes nothing; otherwise the whole file is written in one transaction. `dry_run=true` returns per-row before / after without writing (FR-OB-09). Further rules (review 2026-10-28): files over 2 MB are refused; keys match after trimming and case-folding (the stored spelling is kept; a key matching two records is an error); a Versioned model's export carries a `version` column and an edited export of a record changed since is refused for that row (`entity.stale`); rows are locked while the real import runs; each row is written in a savepoint so a database refusal (another unique column, a value too long) is reported against its row and nothing is kept.
- `export_csv` escapes cells a spreadsheet would run as a formula (starting with `=`, `+`, `-`, `@`, tab or CR, except plain numbers) with a leading apostrophe; import removes that apostrophe again, so an export round-trips.
- Permissions are the PRD's seven coarse keys only (§6); there are no per-operation keys.

```python
class CommandService(Generic[T]):
    """State changes on an aggregate. Orders are never edited by a generic PUT."""
    async def execute(self, cmd: Command) -> Result:
        async with self.uow:
            agg = await self.repo.get(cmd.aggregate_id, for_update=True)   # SELECT … FOR UPDATE
            self.check(agg, cmd)                                           # re-check the rule
            self.machine.transition(agg, cmd.target, actor=cmd.actor, events=self.uow.events)  # §13.6
            await self.uow.session.flush()                                 # audit rows written; no commit
```

(`execute` first checks the command's `permission`; the endpoint or job that opened the unit of work commits — §13.4.)

**Authorization at the service (STEP 1, 2026-10-29).** Every application service (`app/services/`) declares the permission each operation needs — `read_permission` / `write_permission` on CRUD services, `permission` on a `CommandService`, `@requires("sales.…")` on any other method — and checks it itself, **before it reads or writes anything** for the operation (a denied call never flushes; tested). So a job or another service calling it is gated exactly like an HTTP request. The check goes through the unit of work's permission checker; `app/core/deps.py` wires the UniOps role matrix there (`uniops_authz.has_permission`, the same admission rule as `require_permission`: `system_admin` passes, otherwise the user's role union must grant the key; `Actor` carries the token's base role). The system actor passes only where `system_allowed = True` / `@requires(key, system=True)` declares it, and is refused everywhere else. A refusal is `PermissionDenied` → 403 `{"error": {"code": "auth.forbidden", …}}`. `crud_router` enforces nothing of its own — it carries no permission list.

Example command: `SendNow940(order_id, confirm_replacement: bool)` — locks the order, re-checks FR-OB-03c (no live 940 unless a confirmed replacement; never for historical orders), transitions fulfillment `Pending → 940 Generated`, raises `Order940Requested`, audits the user.

Example config resource (whole wiring for one table):

```python
class PartnerService(CrudService[Partner, PartnerRead, PartnerCreate, PartnerUpdate]):   # app/services
    repo_cls = PartnerRepository             # search_fields=("code", "name")
    read_permission = "sales.read"
    write_permission = "sales.config"        # every config CRUD uses the coarse key
    async def validate_create(self, data):
        if data.invoice_method == InvoiceMethod.EMAIL and not data.invoice_email:
            raise BusinessError("partner.invoice_email_required")   # FR-OB-33

router = crud_router("/config/partners", PartnerService, uow=get_uow)   # list/get/create/update/deactivate/export/import
```

### 13.6 State machine

```python
class StateMachine(Generic[S]):
    def __init__(self, transitions: Mapping[S, frozenset[S]], name: str, *, field: str = "status",
                 initial=(), holds: Mapping[S, Iterable[S]] | None = None): ...
    def bind(self, model: type) -> type: ...  # checks `<field>` (+ `<field>_before_hold`) exist
    def can(self, src: S, dst: S) -> bool: ...
    def transition(self, entity, dst: S, *, actor: Actor, events: EventBuffer, reason: str | None = None) -> StatusChanged:
        """Raises BusinessError('state.invalid_transition') if dst is not allowed;
        sets the field, records old/new in the audit, raises a StatusChanged event."""
```

Review 2026-10-28: `events` is required (a transition nobody hears about is a bug). The machines are declared before the models exist, so the columns are checked by `bind(Model)`, called next to the model: it fails at import when `<field>` or, for a machine with hold states, `<field>_before_hold` is missing, and registers the fields with the audit trail — so every transition writes an `sales_audit` old/new row, also on models that are only `CreationAudited` (documents, ACKs). `transition` refuses an entity whose class was never bound. A table edge into a hold state counts as a hold entry, so the status held before is always stored.

The machines of PRD 9.1 — fulfillment, invoice, transfer, inbound file, outbound file, ACK — are declared in business code (`app/domain/status.py`), not in the platform. A unit test reads the PRD itself: the fulfillment and invoice tables row by row, the transfer, file and ACK lines by their arrow chains, with each further clause matched verbatim against a written-down meaning (a reworded PRD fails until the mapping is reviewed). Every (from, to) pair of every machine is then checked against that PRD-derived set plus the listed additions. Order state stays a computed view (D17).

### 13.7 Domain events and outbox

- `EventBuffer.raise_(event)` collects events during a unit of work. **Internal handlers** (close the fulfilment, create or complete a task, recompute a worklist entry) run inside the same transaction just before COMMIT.
- **External effects** (SFTP put, Shopify call, external email) are written as rows to `sales_outbox` in the same transaction and executed by the scheduler after commit, so a crash between commit and the call loses nothing. Each row has kind, payload, the entity it acts for, attempts, last_error, status (PENDING / IN_FLIGHT / DONE / FAILED / CANCELLED / SUPPRESSED) and is visible on the health page.
- **At most once** (review 2026-10-28). Before the effect runs, the row is committed as IN_FLIGHT with its attempt counted and a lease; the outcome is written in a second transaction. If that never happens (crash, lost connection, failed commit, shutdown during the call), the row stays IN_FLIGHT and, after the lease, is closed as FAILED "outcome unknown" with `on_failed` (task) — for every kind, because a lost answer may hide a delivered message. `on_failed` / `on_suppressed` run in a savepoint: a hook that raises loses its own writes, never the row's status.
- **Cancellation.** `Outbox.cancel_for(entity)` cancels the entity's PENDING rows in the caller's transaction and reports rows already IN_FLIGHT (the business command refuses or holds instead); used for FR-OB-18 (940 Generated → Cancelled) and an invoice Blocked while Queued. A kind's `pre_check(payload, uow)` re-reads the entity under lock just before the effect and cancels the row when the state no longer wants it.
- **Suppressed** (product owner, 2026-10-28). A call refused by the guard (§5.5, §13.9) raises `Suppressed`; the row ends SUPPRESSED (not DONE) and `on_suppressed` sets the document's status Suppressed (PRD 9.1). Turning the switch on never sends these: a person reviews them and requeues (`Outbox.requeue` takes FAILED and SUPPRESSED rows); requeued while the switch is still off, the row ends SUPPRESSED again.
- **Retry policy per kind** (product owner, 2026-10-27):
  - **SFTP puts** of 940 / 943 / 810 are attempted **once**. A failure marks the document Failed and raises a task; only a user resends (Send 940 now / file register) — FR-OB-24.
  - **Shopify writes** (fulfillment, tracking, archive) are attempted **once**. A failure marks the outbox row Failed and raises a task; a user retries it from the order or the outbox list. No automatic retry, so a timeout whose request actually succeeded can never create a duplicate fulfillment; the manual retry first reads the order's fulfillments and only writes what is missing.
  - **External email** has a bounded automatic retry (configured count, back-off) **only when the SMTP server did not accept the message**: connection failure, or refusal before the DATA phase completes. Once the server has accepted the message (2xx after DATA), it is never resent automatically, even if a later step fails; it is marked DONE and any later error is only logged.

### 13.8 Strategies and registries

```python
class InboundParser(Protocol):
    kind: DocumentKind                       # 850_JSON | SHOPIFY_ORDER_JSON | 945_EDI | 944_EDI | 997_EDI
    def parse(self, doc: StoredDocument) -> ParsedDocument: ...
    async def apply(self, parsed: ParsedDocument, uow: UnitOfWork) -> ApplyResult: ...

class OutboundBuilder(Protocol):
    kind: Literal["940_SHOPIFY", "940_RETAILER", "943", "810_JSON"]
    def build(self, aggregate, ctx: BuildContext) -> OutboundFile: ...
    def validate(self, f: OutboundFile) -> list[ValidationIssue]: ...   # FR-OB-29a

class InvoiceSender(Protocol):
    method: InvoiceMethod                    # 810_JSON | EMAIL | NONE
    async def send(self, invoice: Invoice, uow: UnitOfWork) -> None: ...

PARSERS: Registry[DocumentKind, InboundParser]
BUILDERS: Registry[str, OutboundBuilder]           # chosen by partner channel
INVOICE_SENDERS: Registry[InvoiceMethod, InvoiceSender]   # chosen by partner invoice_method
```

### 13.9 Gateways

```python
class SftpGateway(Protocol):
    async def list(self, conn: Connection, folder: str) -> list[RemoteFile]: ...
    async def get(self, conn: Connection, path: str) -> bytes: ...
    async def put(self, conn: Connection, folder: str, name: str, data: bytes) -> None: ...
    async def move(self, conn: Connection, src: str, dst: str) -> None: ...
    async def delete(self, conn: Connection, path: str) -> None: ...

class ShopifyGateway(Protocol):
    async def orders_since(self, cursor: datetime) -> AsyncIterator[ShopifyPage]: ...
    async def create_fulfillment(self, order_id: str, tracking: Tracking | None) -> str: ...
    async def update_tracking(self, fulfillment_id: str, tracking: Tracking) -> None: ...
    async def archive(self, order_id: str) -> None: ...

class EmailGateway(Protocol):
    async def send_external(self, msg: ExternalEmail) -> None: ...

class DocumentStore(Protocol):
    async def put(self, raw: bytes, meta: DocumentMeta) -> StoredDocument | Duplicate: ...
    async def get(self, id: int) -> StoredDocument: ...
```

Each has a real implementation (asyncssh, httpx, aiosmtplib, `sales_documents`) and a fake (in-memory, records calls) for tests. **Timing:** P0a delivers the Protocols, the in-memory fakes, `GuardedGateway` and the outbox with its per-kind retry policy (tested through fakes that raise `NotAccepted` / `AcceptedThenFailed`); the real SFTP gateway (asyncssh) comes with P2, the real Shopify gateway (httpx) with P3, the real email gateway (aiosmtplib) with P6/P8 where it is first used, and `DbDocumentStore` with `sales02` (P2). `SftpGateway` and `EmailGateway`, their fakes, and each one's guarded-write and read lists and "would have …" summary live in `app/integrations/sftp.py` and `app/integrations/email.py` (STEP 1, 2026-10-29); `ShopifyGateway` will be `app/integrations/shopify`. `GuardedGateway` itself stays generic in `app/platform/guard.py` — it knows no integration: each integration module hands it its lists. It wraps SFTP, Shopify and Email and is the **only** place that reads `SALES_EXTERNAL_EMAIL_ENABLED` and `SALES_PARTNER_WRITES_ENABLED` to decide a send (§5.5). It is an allow-list: the composition root names the guarded writes and the pass-through reads; any other method, private attribute or the wrapped client is an AttributeError. A refused call is logged, handed to `on_refused` — the composition root's refusal sink, which writes the `sales_log` "would have sent / written" row (email: recipients, subject, body hash; SFTP: connection, folder, name, size, hash; the sink is wired with `sales_log` in P0) — and raises `Suppressed`. One further reader: the composition root passes `notify = (SALES_EXTERNAL_EMAIL_ENABLED is true)` to the TaskService (§3.5, product owner 2026-10-28).

### 13.10 X12 toolkit

`app/integrations/x12/` (moved out of the platform, STEP 1): `x12.Envelope` (ISA/GS/ST/SE/GE/IEA with the A.2 values), `x12.read(raw) -> Interchange` (separators read from the ISA, FR-OB-26), `x12.Segment` builder with element escaping, control numbers from `sales_x12_control_seq` (D10), zero-padding helpers used by both building and matching (FR-OB-22a / 28). Separator characters are cleaned with the envelope's own separators. Output is strict ASCII (product owner, 2026-10-28): accented and common letters are transliterated (é → e, ß → ss, curly quotes, dashes, no-break spaces) and each change is noted for the document's log; a character with no ASCII equivalent raises `UnmappableCharacter` naming the element (e.g. `N102`) and the character, so the builder's validation fails and the order goes to Review with a task the person can act on (edit the address). "?" is never written.

### 13.11 Pipeline

```python
class Step(Protocol):
    name: str
    async def run(self, ctx: CycleContext) -> StepResult: ...

async def isolated(unit_name: str, fn, *, uow_factory, log) -> UnitOutcome:
    """Runs fn in its own unit of work; on error rolls back, logs to sales_log with
    context, raises/refreshes the problem task, and returns — never re-raises
    (FR-OB-50)."""
```

The intake chain (list → identity check → stability → download → hash/store → commit → after-read → classify, FR-OB-70..76) and the cycle (fetch → apply → time rules → build → outbox → write-back) are lists of `Step`s; each unit inside a step goes through `isolated`. `isolated` is the job's unit boundary and the only place in a cycle that commits (once per unit, FR-OB-50); the function it runs, and every service that function calls, only flushes (§13.4).

### 13.12 Scheduled job

*(2026-11-02)* A job may carry a `scope` (e.g. one connection): single flight is per (job, scope) — the advisory lock takes the scope's hash as its second key, and the RUNNING check and stale closure look at that scope only — and every run row records its scope. `due(session, now)` decides an unforced claim (default: the interval since the last scheduled start, unless paused — the sketch below); `on_closed(run)` is called with every closed run, failures included (best effort, logged).

```python
class ScheduledJob(ABC):
    lock_key: int
    interval_setting: str                    # read from sales_settings each tick
    @abstractmethod
    async def run_once(self, run: RunRecord) -> RunSummary: ...
    async def tick(self) -> None:            # advisory xact lock, close stale RUNNING runs,
                                             # open run, heartbeat, run_once, close run
```

Started with `spawn()` in the lifespan, stopped with `drain()` before `engine.dispose()` (§4.1). The cycle and the outbox worker are two `ScheduledJob`s sharing the same lock discipline. A failing heartbeat is logged and retried, never escapes the tick, and the run is still closed; the heartbeat stops by itself when its run is no longer RUNNING; a run cancelled by shutdown is closed as FAILED "interrupted" so a restart is not blocked for `stale_after`; one failing tick never ends `loop()`.

### 13.13 Cross-cutting

- **Audit:** an SQLAlchemy `before_flush` listener writes `sales_audit` for every change to an `Audited` entity (who, what, before/after); commands add their reason.
- **Actors:** `Actor(user_id, name, role)` (role = the token's base role, for the permission check); scheduler work runs as `SYSTEM_ACTOR`, recorded as such (never as a person), and passes a permission check only where it is declared (§13.5).
- **Settings registry:** typed `Setting[T](key, default, validator, safe)` declarations; values in `sales_settings`; read through one cached accessor, emptied when a change commits. A stored value that no longer parses is **safe-off** (product owner, 2026-10-28): the read returns the setting's `safe` value — never a default that may be "on" (booleans: False; every other setting declares one; `None` = off, e.g. a paused schedule) — and `on_invalid` raises a task.
- **Feature flags:** e.g. `shopify_writeback_enabled`, `shadow_mode` — settings with an audit trail.
- **Errors:** `BusinessError(code, message, details)` → one JSON shape `{ "error": { "code", "message", "details" } }` with HTTP 409/422; unexpected errors → 500 with a correlation id.
- **TaskService** (`app/integrations/epms_tasks.py` — it writes the shared EPMS table, so it is an integration, not platform): `raise_problem(event, entity, context)` (problem key `<prefix>:<event>:<entity>`, one open task, refresh if open), `resolve_problem(event, entity)` (auto-complete) — §3.5. The prefix (`SALES`), `document_type` (`sales`, ≤ 10), the `notify()` callable (D-b, §3.5) and task-type names are supplied by business code; the guard decorator (§13.9) likewise receives its switch names (`SALES_EXTERNAL_EMAIL_ENABLED`, `SALES_PARTNER_WRITES_ENABLED`) from the composition root, so no Sales or Order Bridge name appears inside `app/platform/`.

### 13.14 Dependency injection

FastAPI `Depends` for request scope plus one composition root, `app/container.py` (business code: the switch names live there), built with the helpers in `app/platform/container.py`:

```python
def build_container(settings: Settings) -> Container:
    sftp = GuardedSftp(RealSftp(...), settings) if settings.ENVIRONMENT != "test" else FakeSftp()
    shopify = GuardedShopify(RealShopify(...), settings) if ... else FakeShopify()
    email = GuardedEmail(RealEmail(po_smtp_settings), settings) if ... else FakeEmail()
    return Container(sftp=sftp, shopify=shopify, email=email, documents=DbDocumentStore(), ...)
```

The scheduler and the API use the same factories. No DI framework.

### 13.15 UI library

- **Location:** `sales/src/platform-ui/` (product owner, 2026-10-27; `packages/ui` and `libs/ui` are dropped). It is **not** under `packages/` and **not** in the root npm workspace, so the root `package-lock.json` never changes. It sits inside `src/` rather than beside it (`sales/platform-ui/`) because there it is covered with no extra configuration by sales's own `tsconfig.app.json` (typecheck), Vite (build and an `@/platform-ui` alias) and Tailwind's source scanning, so nothing outside `src/` has to be wired and nothing can silently drop out of the typecheck or the CSS. The folder rules of §13 keep it separable.
- Builds **on top of `@uniops/shell`**: reuses its Button, Badge, Card, Input, Label, FormField, Pagination, Skeleton and tokens. No second Button or any duplicate of a shell component; `packages/shell` is not modified. **One place for every component** (STEP 1, 2026-10-29): `platform-ui/primitives/shell.ts` re-exports them (thin re-exports with their prop types — never copies), so Sales imports every component from `@/platform-ui`; ESLint bans `@uniops/shell` anywhere else in Sales except the app shell (`src/main.tsx`, `src/components/layout/`, `src/app/`, which wire the shell's tabs and routes). A variant Sales needs is a wrapper in `primitives/`, never a fork.
- **Three layers** (STEP 1, 2026-10-29), one way: `primitives/` — visual only (Dialog, Sheet, Popover, Combobox, Tabs, Toast, StatusBadge, States, the `DataTable` view, the shell re-exports); may import primitives, `lib` and `@uniops/shell`, never API, permission or query code. `resource/` — `ResourceClient`, `useCrudResource`, `querySpec`, `businessError`; may import resource and `lib`. `patterns/` — composes the two: `ActionButton`, `ConfirmAction`, `SchemaForm`, `QueryTable` (the `DataTable` view adapted to `QuerySpec`), `CrudPage`, `PermissionGate` / `useCan`, `ErrorMessage` / `ErrorView`; may import patterns, primitives, resource and `lib`. The library never imports features, pages or app code. Enforced by ESLint and the vitest import scan, each proved by planted violations.
- **Features:** `src/features/{orders, fulfillment, invoices, transfers, configuration}/` hold the screens of each area as they are built (each starts with a README); they and `src/pages` use `@/platform-ui` only.
- **shadcn CLI:** `sales/components.json` points it at `@/platform-ui/primitives` (css `src/index.css`); it has not been run. After any use, `tools/check-scope.sh` must still show the repository root, `packages/shell` and the root lock file unchanged.
- Adds: Radix-based Dialog, Sheet, Popover, Tabs, Combobox, Toast, `ConfirmAction`; `DataTable<T>` on TanStack Table (the view) and `QueryTable<T>`, its backend adapter (sort/filter/page → `QuerySpec`); `CrudPage<T>` (list + form + CSV import with dry-run preview); `SchemaForm` from zod (config forms only); `ResourceClient<T>` / `useCrudResource` working on types generated from the sales-api OpenAPI schema (the generated types live in business code, `src/api/generated`, and are passed in); `StatusBadge` taking a status → style map (the 9.1 map is supplied by business code); `ActionButton` (disabled while pending, checks a permission passed in); `PermissionGate` / `useCan`; error display by `BusinessError` code; Empty / Error states (Skeleton from the shell). Light theme only; tokens dark-ready. No TreeView.
- Build facts: `sales/Dockerfile` copies `packages/shell` and `sales` as inventory's does — **no extra COPY**, the library is part of `sales`; `sales/src/index.css` keeps only the `@source` line for `packages/shell` (the library is inside `src/` and is scanned automatically; a build check confirms its classes are present in the output CSS); Radix, `@tanstack/react-table`, `react-hook-form` and `zod` are pinned in `sales/package.json` only; typecheck runs with sales's own `tsc` and `tsconfig.app.json` (TypeScript ~6.0), and the check verifies that a deliberate type error inside `src/platform-ui` is reported (guards against the TS5101 "diagnostics skipped" trap). Found in P0a (2026-10-27): `@hookform/resolvers` is not used — its optional peer `ajv@8` leaves a lock file npm 10's `npm ci` rejects — so the library has a small zod resolver of its own; vitest is held at `~4.0.18` because every 4.1.x crashes npm 10 (the npm of `node:20`) while resolving peers, and 4.0 runs on its own nested vite 7, so `vitest.config.ts` has no React plugin (automatic JSX only).
- **Reuse (2026-11-02).** A list page is `ListPage` (patterns): the query (keys from `queryKeys`, derived from the resource path — never written by hand), views, column menus and search from `/meta` (no hand-written `sortable` lists), the open row taken from the refreshed list, the detail sheet, `rowActions`, `fixed` filters (no views). A new read-only list is one `<ListPage …/>` plus its columns; `CrudPage` is a `ListPage` whose detail is the record's form. Values are shown through the standard cells and formatters (`DateTimeCell`, `DateCell` — a date-only value never goes through `new Date` —, `MoneyCell`, `StatusCell` with one shared status → tone map and a word-based fallback for new enum values; `formatDateTime / formatDate / formatTime / formatMoney`); headers through `PageHeader`, record details through `DetailList`. ESLint refuses `toLocaleString*` and `new Date(` in `src/features/` (tests exempt), and check-scope plants both.
- **Making and editing records (the user's rule, 2026-11-02).** A page never carries a form. New is `ListPage`'s `create` slot: the standard New button at the end of the toolbar (permission-gated), the form in the standard Sheet; on save the sheet closes, the list refreshes and the new record's detail opens — `CrudPage` uses the same slot, so there is one New mechanism. A record's form is `SchemaForm`; a record with lines is `MasterDetailForm` (SchemaForm's header fields + `EditableTable` lines + an optional preview / `blocked` reason; one submit, field and line errors, a ref-guarded submit) — manual orders, transfers, invoice edits, delivery confirmation, returns. Editing a Draft opens the same form in its detail Sheet; a step with its own fields opens a Dialog; an action that needs a reason is `ConfirmAction` with `reason`. Manual orders live in Orders (create slot "New manual order", `sales.order.operate`; the system view "Manual drafts"; Submit as a row and detail action); `/orders/manual` redirects to that view (`src/app/oldAddresses.ts`). ESLint refuses a raw `<form>` in features and pages (check-scope plants one in each); where a form is rendered is in platform-ui/README.md. **Lookups (2026-11-02):** `Autocomplete` opened with nothing typed shows the first page (the loader's answer to ""; `minChars` applies to typed text only) with a "Type to search…" hint; options carry label + hint and the chosen value shows as "label — hint" (a value set from outside is looked up once, by its value, else in the first page). `selectColumn` is an EditableTable column whose options depend on the row (a manual order's unit: the item's active units as "CASE (6 EA)", disabled until an item is chosen, back to EA when the item changes). The manual order's customer is "Customer" (MANUAL-channel partners by name — code as the hint; the only one is preselected; none → a message linking to Configuration → Partners), followed by "Customer PO / reference" (shown in the order detail and as the list's "Customer PO / ref." column). **Addresses (2026-11-02):** `AddressFields` (in `MasterDetailForm` as `addresses`) is the one address form — name, attention / contact, phone, address, line 2, city, province (a Select of the Canadian provinces when the country is CA), postal code (A1A 1A1 when CA), country (default CA). A manual order's "Ship to" is required: the server checks what the 940 needs (`AddressIn`: name, address1, city, a CA province code, a CA postal code normalised to "A1A 1A1") and refuses field by field (`details.errors[].field = "ship_to.zip"`, shown on the input); it is editable while the order is a Draft. The order detail shows Ship to and Bill to as formatted addresses (`AddressView`) with their origin: Shopify's shipping / billing address; the 850's ShipTo with its GLN and the DC it routes to (`dc_number`, Walmart) and BillTo with its code; "entered by hand" for a manual order (which has no bill-to). **Address checks, offline (the user, 2026-11-02 — no address book, no address service; Shopify consumers are not stored as customers, the address stays a snapshot on each order):** a Canadian postal code's first letter names its province (A NL, B NS, C PE, E NB, G/H/J QC, K/L/M/N/P ON, R MB, S SK, T AB, V BC, X NT/NU, Y YT; `app/domain/address.py`, mirrored in `patterns/address.ts`). A manual order whose postal code is in another province is refused on the postal code (server and form); its phone is required (carriers need it). A PO box ("PO Box", "P.O. Box", "Post Office Box", "CP"/"C.P." nnn, "Case postale", "Box nnn" in address 1 or 2) is never refused: the manual form warns and asks for a confirmation ("Ship to this PO box anyway", not sent); an imported order's detail shows the flag (`ship_to_flags`). An imported order (Shopify, 850) whose ship-to postal code is in another province goes to Review before any 940 (FR-OB-29a) with the reason "ship-to postal code … is in …, not …"; fixed at the source, the next update or Re-check clears it. In the shadow run the order is set to Review and no task is raised (as for every order problem).

**Order ref and partner names (user 2026-11-03; `sales15`).** The visible identifier of an order is its Order ref — `sales_orders.source_reference`, A.3's source reference: the partner's abbreviation, "-", the source order number (a manual order: its internal number), unique, set once at creation (a later abbreviation change leaves existing refs; new orders use the new one). It titles the order everywhere (lists, detail, tasks, notifications, PDFs); the internal `order_number` stays where A.9 / A.10 / A.3 need it (810 Invoice and invoice number IN-, 940 W05, file names, the packing list) and shows as "Internal no.". The partner abbreviation is required (letters and digits, upper-cased, unique ignoring case); an active partner without one gets the task `sales_partner_no_abbrev` (the system job; completed when it is set) and its orders go to review. Lists carry the partner's name (`partner_name`, a SQL column — searchable, sortable, groupable) and show "Name (CODE)". Order lines show the PO unit price, unit and amount with a total. Invoices: each line shows the PO price (850 price × CASE factor ÷ the order unit's factor) beside the invoice price, the difference marked; a changed price needs a reason (the audit row carries it; the log lists each line's old and new price); `delivery_number` (≤ 50, the retailer's proof-of-delivery number) is edited with the date and printed on the PDFs — A.9's 810 has no field for it.

**Totals, the order lifecycle, Lots and recall (user 2026-11-03; `sales16`).** Lists: partners shown by name, the code in a tooltip (the detail keeps "Name (CODE)"). A repository may declare `summable` fields (and `sum_currency`): the list response then carries `totals` — the sums over the whole filtered result, one entry per currency, never added across currencies — and each group its own (`groups[].totals`); `/meta` marks the field `summable`. The list footer shows them under the visible summed columns; group headers after the count. Orders: the Total column is `total_amount` (the source's total, else the lines at the PO price — an 850 or manual order has no total). Invoices: Amount. The order detail's Lines is one lifecycle row per line — ordered (qty, unit, PO price, amount), EA, shipped EA (945), delivered EA (confirmation), invoiced (qty, unit, price, amount from the invoice's lines), and a status (Open, Shipped / Short / Over shipped, Delivered / Short delivered, Invoiced, Removed) — each expandable to the 945 rows packed for it (SSCC, lot, expiry, qty, tracking, ship date, the 945; a shadow-run or second 945 marked); less-used columns start hidden (LocalTable `initiallyHidden`). Operations → Lots: a read model (`app/models/lots.py`, a SELECT over `sales_shipment_rows` with its shipment, order and partner — no table) listing every 945 row with the order ref, partner, ship-to city and postal code (the 945's, else the order's); searchable, groupable by lot / item / partner / order; views "Expiring soon" (`days_to_expiry` 0–30) and "By lot"; a recall is the filter `lot:in:…` (≤ 100 lots) with the orders and customers it reached and `GET /lots/export.csv`. Each shipment prints its packing list (`GET /orders/{id}/shipments/{sid}/packing-list.pdf`: pallets → items → lots → expiry → qty, pallet and shipment totals, carrier, BOL, tracking, the 945 and its N9*E1). `sales_shipments.our_reference` keeps the 945's header N9*E1 (the reference the 940 went with); `sales16` fills it for shipments already received from their stored 945. Nothing deletes shipment rows or 945 documents (FR-OB-05; a second 945 adds rows, the first stay).

**Shopify finance (user 2026-11-03; `sales17`).** Read-only, with read_orders: the order pages now also carry `source_name`, `payment_gateway_names`, `discount_codes`, `tax_lines` (refunds already came whole); for every order it imports the pass reads `orders/{id}/transactions.json`, and `services/shopify_finance.store_finance` upserts by Shopify id — `sales_order_payments` (every transaction, a refund's linked to its refund), `sales_order_refunds` with `sales_order_refund_lines` and `sales_order_refund_adjustments` (lines incl. tax, shipping refund, other adjustments, the refund's successful transactions) — and on the order `source_name`, `tags`, `payment_gateways`, `discount_codes`, `tax_lines`, `original_total` (total_price), `total_refunded` (successful refund transactions), `net_amount` (original − refunded), `finance_updated_at`; per line `tax_amount`, `discount_amount`, `tax_lines`. A refund bumps the order's `updated_at`, so the pass sees it; a failed transactions read fails the order (the cursor stays). After the pages the pass fills up to 50 orders whose finance is missing or stale (`orders/{id}.json` + transactions). FR-OB-11b is untouched (the order sync still sets the lines). **No card data**: `integrations/shopify.strip_card_data` drops `payment_details`, `receipt`, AVS / CVV codes and any `credit_card_*` key from every JSON the client returns (pages included — never `gift_card`), and `store_finance` strips again. A read answered 429 waits Retry-After (≤ 10 s) and retries up to 4 times. Orders: Net, Refunded and Payment status columns (Net and Refunded summed), Sales channel and Tags fields (tags searched), views "Refunded" and "Partially refunded"; the detail's "Payments & refunds". "Groups" = tags + sales channel; customer segments would need read_customers and are not read.

**Backordered, the line's invoice price, Internal no. everywhere (user 2026-11-03; `sales18`).** *Backordered* is derived, not stored: a line's `qty_ea − shipped_ea` (≥ 0) once an applied 945 set `shipped_ea` (NULL before; removed lines, shadow-run and second 945s never count — they never set `shipped_ea`); `Order.backordered_ea_sql` sums it (list column "Backordered EA", sortable / filterable, view "Backordered (partially shipped)" = > 0). W12 was checked against Radiant's 12 real 945s on dev2: all `CC`, W12-02 = W12-03 and W12-04 always 0 (no real partial; the `CP` ones are synthetic test files), and W12-02 repeats the line's ordered quantity on each split row — so W12 is not used for it. FR-OB-25 / D9 / D18 are unchanged: a short 945 still closes the fulfilment (Shipped) and raises the one shortfall task; no fulfilment status was added — the UI marks a Shipped order with backorder "Partially shipped", and no new task. *Invoice price*: `sales_order_lines.invoice_unit_price` (per order unit like the PO price; NULL = the PO price), set by `PUT /orders/{id}/invoice-prices` (`sales.invoice.manage`) only while no invoice exists and the order is not cancelled; a price ≠ PO needs a reason (`invoice.reason_required`), audited and logged; equal to the PO stores NULL. The draft (`Invoicing._build`) takes it instead of the PO price and converts to per case as before; after the draft exists the order line is read-only and the price is changed on the invoice (`order.invoice_exists`). 810 and PDFs read invoice lines as before. *Internal no.*: a column beside Order ref (no fallback between them) on Orders, Invoices, Lots and Shadow compare, searched exactly (`Repository.search_numbers`: a digits-only search equals the integer column) and sortable; task, notification and log text name orders by `Order.label` ("LCL-4887314746 (internal no. 7)"); the invoice and packing-list PDFs already carried both. EDI content unchanged.

### 13.16 Module template

`tools/new-module/` generates a service skeleton (FastAPI app, config, health, alembic with its own version table, Dockerfile, tests/conftest refusing non-`_test` databases) and a front-end skeleton (session hand-off, shell tabs, permission-gated nav, branding, ESLint), and prints the wiring list of §2 / §14.2 as a checklist with exact snippets. It writes only the two new folders and never edits an existing file.

**The template contains no platform code** (product owner, 2026-10-27). `--platform=none|inplace` decides what the skeleton gets:

- `none` — the default, and the only value allowed for any other module until the §14.7 decision: the skeleton has no platform imports.
- `inplace` — for `sales-api` / `sales` only (any other target is refused with an error): the skeleton imports `app.platform` / `@/platform-ui`, and the platform folders are written into it by hand (P0a). Nothing is ever copied.

P0a generates the bare `sales-api` and `sales` skeletons with it, so the template is proven before P0 wires them. The STEP 1 layers (§13: `app/services`, `app/schemas`, `app/integrations`; `src/platform-ui/{primitives,patterns,resource}`, `src/features`) are written into `sales-api` / `sales` by hand; the template is unchanged by them — a `--platform=none` module has no platform layers to split.

### 13.17 Decisions on the platform design (product owner, 2026-10-27 — all closed)

1. **Retries:** SFTP files and Shopify writes are sent once, then Failed + task + manual retry; external email retries automatically only when the SMTP server did not accept the message (§13.7).
2. **Soft delete:** the platform provides `SoftDeletable`; it is available but not used in v1 (§13.1). Drafts follow PRD 9.1 (Draft → Cancelled).
3. **Library location:** the UI library is `sales/src/platform-ui/` and the back-end foundation is `sales-api/app/platform/`, both under the extraction-ready rules of §13 (§13.15).

### 13.18 What the first slice needed (P1 Partners, 2026-10-29)

Input to the decision on trimming the platform (§14.7). One configuration table end to end: list, form, read-only view, audit history, CSV.

- **Used as is.** Back end: `Audited` + `Versioned` + `Deactivatable`, `Repository` with `QuerySpec` / `FilterRule`, `CrudService` (hooks `validate_create`, `before_update`; CSV export / import with dry run and stale-version refusal), `crud_router`, `UnitOfWorkFactory` with `AuditTrail` and the permission checker, `authorize`, `BusinessError` / `InvalidInput`. Front end: `CrudPage`, `QueryTable` / `DataTable`, `SchemaForm` with its zod resolver, `createResourceClient` / `useCrudResource`, `PermissionProvider` / `useCan` / `ActionButton`, `ConfirmAction`, `Sheet`, `Dialog`, `Badge`, `ErrorView` / `EmptyState` / `Skeleton`, `useToast`.
- **Missing, added in P1** (each with its test): request-schema refusals in the one error shape (`request.invalid` with `details.errors` per field) and `SchemaForm` putting each on its input; the CSV key column never validated as an update field (an update DTO that forbids extra fields refused every import row); jsonb cells exported as JSON (Python's repr did not read back); the operation as the default audit reason (CRUD writes recorded none); `CrudPage` opening a row read-only without the write permission, a `renderDetail` slot (the audit panel) and `singular`; `SchemaForm.readOnly`. In the business code: an overridable unit-of-work factory dependency (tests could not reach the request's unit of work), the record history read (`app/services/audit.py`), and `upload` / `download` on the API client.
- **Still missing.** OpenAPI type generation (§13.15): the feature's types are hand-written mirrors of the DTOs. The audit history route is written per resource; `crud_router` could offer it once a second table needs it. Code uniqueness ignoring case is checked by the service only (the database index is exact).
- **P2 (scheduler and intake) added:** `ScheduledJob.claim()` / `execute()` (a "Run now" claims through the scheduler's own single-flight path in the request and runs in the background; `tick` is the two together); `read_router` (list · get for a `ReadOnlyService`: documents, runs, log); `FakeSftp` gives every file a modified time and answers `host_key` / `check`; `CrudPage` takes the open record from the refreshed list, so an action in its detail panel (pin, clear latch) never leaves the form with a stale version. Used for the first time: `ScheduledJob` + `Background`, `pipeline.isolated`, `GuardedGateway` / `container.gateway` with a refusal sink writing `sales_log`, `OutboxWorker` (wired, no kinds yet), `SettingsRegistry` (interval, pause, operator), `StateMachine.bind` (documents), `Tabs`. A business-side note: `sales_documents.content` is deferred and `has_content` is SQL, so lists never load file bytes.
- **P3 (orders) added:** composite natural keys for CSV (`csv_key` may be a tuple — partner codes, DC routing) and four CrudService hooks: `build` / `apply_changes` (create, update and import alike — an item's units are a child set), `csv_value` (export of a non-column value: `EA=1; CASE=6`) and `_same` (import's unchanged test for such a value). Used for the first time: `StateMachine.transition` on orders and documents, `Quantity.to_base` / `UnitTable` (EA conversion, never rounded), `historical_column` (the order list).
- **Not used yet**, with the phase that first needs it: `StateMachine` / `CommandService` / events (P3–P5), `Outbox` / `OutboxWorker`, `GuardedGateway` / `container.gateway`, `pipeline`, `ScheduledJob` (P2), `SettingsRegistry` (P1 settings, P2 scheduler), `Quantity` / `Money` (P3–P6), `historical_column` (P9); UI `StatusBadge` (P2 onward), `Combobox`, `Popover`, `Tabs` (order screens). `SoftDeletable` is unused by design in v1 (§13.1). Nothing is proposed for removal before those phases have used, or failed to use, each piece.

### 13.19 List query contract (every list endpoint; 2026-10-30)

Every list in the module — Partners, Items, Partner codes, DC routing, Connections, Orders, Review, Documents, Runs, Log — speaks one contract, declared once per resource on its repository and served by the platform (`app/platform/query.py`, `repository.py`; client `sales/src/platform-ui`).

**Declaration (ABP style).** A repository names its `list_fields` (model columns that are sortable and filterable), optional `labels` and `enums` (a field and its values, from the domain constants), plus `search_fields`, `default_sort` and any special `filterable` / `sortable` entry. Only declared keys reach SQL, as columns; anything else is `422 query.unknown_field`. `secret_name` and `host_key` are never list fields.

**Request.**

| Parameter | Form | Meaning |
| --- | --- | --- |
| `filter` | `field:op:value`, repeated | different filters are AND (also two on one field, e.g. `gte` and `lte`) |
| value | `v1\|v2` | several values: any of them for the positive operators, none of them for `ne` / `not_contains` / `not_in`; a literal `\|` is written `\\|`, a backslash `\\` |
| `between` | `low..high` | either side may be empty; `range` is the old name and still accepted |
| `is_null` / `not_null` | `field:is_null:` | no value |
| `sort` | `a,-b` | priority = order; `-` descending; ties broken by id |
| `search` | text | the quick search over `search_fields` (ILIKE) |
| `page`, `page_size` | | `page_size` ≤ 200, `page` ≤ 10 000 |

Limits: 20 filters, 100 values per filter, 200 characters per value, 100 for `search`.

**Operators by field type** (the type comes from the column; a declared enum is `enum`):

| Type | Operators |
| --- | --- |
| text | `contains`, `not_contains`, `starts_with`, `ends_with` (case-insensitive, `%` and `_` literal), `eq`, `ne` (exact), `in`, `not_in`, `is_null`, `not_null` |
| number / date / datetime | `eq`, `ne`, `gt`, `gte`, `lt`, `lte` (one value), `between`, `in`, `not_in`, `is_null`, `not_null` |
| enum | `eq`, `ne`, `in`, `not_in`, `is_null`, `not_null` |
| bool | `eq`, `ne`, `is_null`, `not_null` |

Values are cast to the column type before SQL: a bad value is `422 query.bad_value`, never a database error. A datetime without a time zone is UTC. The negative operators (`ne`, `not_in`, `not_contains`) include rows where the field is empty (an empty value is not any of the values).

**Response.** `{items, total}` (plus `groups`, `groups_truncated` when grouped). `GET <resource>/meta` (read permission) returns `{version, fields, searchable, default_sort}`; each field has `name, label, type, enum_values, filterable, sortable, groupable, operators`. The fields are the read schema's scalar fields in its order, then any other declared key, each once.

**Client.** `QueryTable` takes the resource and builds from `/meta`: a menu per column (Search · Sort ascending / descending · Hide column), the chips row (SORTING — order is priority, click flips, × removes; SEARCH — one chip per filter plus the quick search; filters the page sets itself are shown without ×), the search panel (tick a field → operator → values as tags; Search applies, Clear resets the panel), the column chooser (the eye; a hidden column stays searchable and sortable; fields the page does not list start hidden). The page's own columns keep their titles and cells. The state of a list is one versioned JSON object, `ViewState` (`version, sort, filters, search, pageSize, columns, hidden`; `resource/viewState.ts`); the request is serialised from it. The browser address bar is not synced (keep-alive tabs).

**Grouping (Stage B, 2026-10-30).** `group=field,-field2`: at most 3 keys (more → `422 query.too_many_groups`), each from the repository's `groupable` whitelist (else `422 query.not_groupable`; free text, ids and date-times are not groupable — partner, status, kind, flag, a date are). Rows are ordered by the group keys first (empty values last in either direction), then the sort, then id. The response then also carries `groups: [{key: [v1, v2…], count}]` — every group of the whole filtered result in group order, so a header shows its true size whichever page it starts on — and `groups_truncated` (more than 500 groups: the first 500 are counted). Without `group` the response is unchanged. `/meta` marks each field `groupable`. Client: Group ascending / descending in the column menu (not offered for a 4th key), a GROUPING chip row (order = nesting, click flips, × removes), and in the DataTable primitive nested, collapsible group header rows with the count (a collapsed group folds its rows and deeper headers). A group checkbox comes with the first list that has row selection.

**Saved views (2026-10-30).** Personal only. Table `sales_user_views` (migration `sales07_user_views`): id, user_id, resource (the list's path, e.g. `orders`, `config/items`), name, state jsonb (a `ViewState`), is_default, sort, created/updated, version; unique (user_id, resource, lower(name)); at most one default per user and list (partial unique index). Platform module `app/platform/views.py` (model mixin, `ViewService`, `views_router`); the app names the lists that have views (`app/services/views.py`). Endpoints: `GET /views?resource=`, `POST /views`, `PUT /views/{id}?version=`, `DELETE /views/{id}`, `POST /views/{id}/default?on=`. Rules: every read and write is scoped to the caller — another user's view is 404; using a list's views needs that list's read permission; the resource must be a listed list (else `422 view.unknown_resource`); a name is free per user and list whatever its case (else `409 view.name_taken`); at most 50 views per list; the state is checked when saved — current `version` (a trivially older one is upgraded through `MIGRATIONS`), known keys, `pageSize` ≤ 200, ≤ 3 group keys, ≤ 20 filters, and only fields the list can sort / group / filter on (else `422 view.bad_version / view.bad_state / view.unknown_field`) — and flagged `stale` when read if it no longer fits (the client says "saved by an older version: review it, then Save"). Writes go through the audit trail. **System views** are declared with each repository (`presets`: key, name, partial state) and returned in `/meta` (`views`); a test checks that every preset is a state its list accepts. Orders: All, Needs review (status = Review — the old Review worklist; `/review` redirects there), Payment hold, Pending (waiting for the 940), Archive suppressed, Historical (with history included); Documents: All, Inbound failed, Duplicates / not ours, Outbound; Runs: All, Failed or partial; Log: All, Errors; Connections: All, Inactive, Login refused; Partners, Items, DC routing: Active, Inactive, All; Partner codes: All. Client: `useListView(resource)` holds the list's spec and layout, opens a requested view (`requestView`, e.g. from `/review`), else the user's default, else the first system view — before the list's first request; the view picker (left of the toolbar) lists System views and My views, marks unsaved changes with a dot and offers Save (mine; on a system view it is Save as new), Save as new, Reset, and per view of mine Rename, Set / remove as default, Delete (confirmed). A list inside another record (fixed filters, e.g. a run's log) has no views.

**Column order.** `ViewState.columns` is the display order (empty = the page's order); `hidden` the columns switched off; a column the order does not name shows if it is one of the page's own. Columns move by dragging a header onto another (native drag and drop) or with ↑ ↓ in the column chooser (the keyboard way); the order is saved with the view.

## 14. Isolation from other projects

The product owner's condition: the foundation and the UI library are used only by Order Bridge first, and nothing may cause issues for other projects.

### 14.1 Code allow-list

New code only in `sales-api/` (including `app/platform/`), `sales/` (including `src/platform-ui/`), and `tools/new-module/` (plus `tools/check-scope.sh`). No code change in any other service, front end, `packages/shell` or `packages/authz`. Front-end dependencies of the UI library are declared in `sales/package.json` only; nothing is added under `packages/` or to the root npm workspace.

### 14.2 Configuration allow-list (additions only)

| File(s) | Addition |
| --- | --- |
| `*/app/core/config.py` of the 12 existing services | the Order Bridge origin in the `ALLOWED_ORIGINS` default |
| `docker-compose.dev.yml` | `sales-api`, `sales-frontend`, `sales_node_modules`, the origin in the 12 inline lists, `VITE_SALES_URL` for portal-frontend |
| `docker-compose.prod.yml` | `sales-api`, `sales-web`, portal-web build arg |
| `Caddyfile` | two sites |
| `.env.prod.example`, `.env.lan.example`, `epms-api/.env.example` | URLs, origin, the two prod-only switches (false) |
| `make-stack-env.sh`, `migrate-prod.sh`, `check-health.sh`, `reset-db.sh`, `run_tests.sh`, `DEPLOYMENT.md` | one entry each |
| `portal/src/components/layout/navConfig.tsx`, `portal/src/pages/PortalHome.tsx`, `portal/src/lib/api.ts`, `portal/src/pages/admin/AdminPanel.tsx` | nav entry, card, URL / return origin, tagline key |
| `identity-api/alembic/versions/0023_sales_perms.py` | new migration (keys, role, grants) |
| `epms/src/lib/taskTypes.ts`, `epms/src/services/tasks.ts`, `portal/src/pages/PortalHome.tsx` (labels), `epms-api/app/crud/task.py` (`_TASK_NO_STATUS_INVARIANT`) | Order Bridge task types |
| `epms/src/lib/api.ts`, `epms/Dockerfile`, `docker-compose.dev.yml` / `docker-compose.prod.yml` (epms-frontend / epms-web) | the Sales handoff (`SALES_URL`, `salesHandoffHref`) and `VITE_SALES_URL` (P8) |
| `epms-api/app/knowledge/modules.yaml`, `epms-api/tests/test_module_guide.py` | guide-layer entry and its presence check |

### 14.3 Scope check

`tools/check-scope.sh` compares `git diff --name-only <base>...HEAD`, the index and untracked files (renames as delete + add) with the two allow-lists; the default base is `merge-base(origin/main, HEAD)` (falling back to `main`), printed at the top and fails on any other path; for allow-listed config files it also fails if a hunk removes or modifies an existing line (additions only). It fails on any changed lock file other than `sales/package-lock.json` — including the root `package-lock.json` — and on any new folder under `packages/`. It also runs the two one-way-dependency checks of §13 (platform folders must not import business code).

### 14.4 Regression gate (before every phase commit)

1. Build and typecheck all 8 existing front ends (epms, portal, oa, vms, finance, booking, mrp, inventory) before and after the change, each with its own `tsc` and `tsconfig.app.json`; results must be identical.
2. Test suites of every touched existing service in throwaway `*_test` containers: identity-api (migration 0023), epms-api (task-type registration and the guide test).
3. Alembic heads unchanged for every service except identity-api (+1) and the new sales-api.
4. Headless Portal check: every existing module card and nav entry still appears for an admin; Order Bridge is invisible to a user without any of its seven permissions and visible with one.

### 14.5 Runtime isolation

Separate containers (`sales-api`, `sales-web`); own `sales_` tables and `alembic_version_sales`; scheduler off by default (`SALES_SCHEDULER_ENABLED`); partner writes and external email off by default (§5.5); Order Bridge invisible in the Portal until a permission is granted; other services never call sales-api, so a failing sales-api cannot affect them (the only shared write is task rows in `tasks`, done in a savepoint).

### 14.6 Rollback

Stop `sales-api` and `sales-web` and leave the permissions ungranted. No other service needs a change; the added configuration lines are inert without the services.

### 14.7 Extraction later

After go-live a short review decides, piece by piece, whether to move `sales-api/app/platform/` or `sales/src/platform-ui/` into shared packages (as was done with `packages/authz`). Thanks to the folder rules of §13 this is a move plus an import change. Until then no other module imports them.
