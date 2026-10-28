# Order Bridge — technical design

- **Date:** 2026-10-23
- **Status:** Accepted for build (not committed). Decisions X1–X4 accepted 2026-10-23.
- **Requirements:** `docs/prd/ORDER-BRIDGE.md` 1.2 on `dev/amir` (working tree of 2026-10-23, including the uncommitted open-item and D21 changes). Requirement IDs (FR-OB-xx, AC-OB-xx, OB-OI-xx, Dn) refer to that document; this design does not restate business rules, it says how they are built.
- **Platform:** an ABP-inspired foundation (`orders-api/app/platform/`) and a UI library inside the front end (`order-bridge/src/platform-ui/`), used only by Order Bridge first and kept extraction-ready (§13, isolation rules §14).
- **Pattern followed:** the Inventory module (`inventory-api` + `inventory`), the newest standalone module, and the shared conventions in `packages/authz`, `packages/shell`, the Portal launcher and the Task Inbox outbox. The earlier SDM code (`sdm/`, `sdm-api/`) is not reused (D14); its documents were read as background only.

## 1. Design decisions

These are design choices the PRD does not settle. All four were **accepted as recommended on 2026-10-23**.

| # | Question | Decision | Why |
| --- | --- | --- | --- |
| X1 | **Accepted 2026-10-23.** Where are raw documents stored? FR-OB-05 says "the UniOps file store", but file-api today has no content hash, rejects JSON and X12 content types, accepts only an end-user JWT (a background job has none), streams nothing and runs on a separate file server. | Store raw bytes in orders-api's own table `obr_documents` (bytea, sha256 unique). Files are small (EDI and JSON pages are kilobytes; about 30 orders a day). Generated PDFs (invoice, packing list) are produced on demand, not stored. | Meets FR-OB-70/71/72 (store before parse, sha256 dedup, commit before the after-read action) inside one transaction, which file-api cannot do. Alternative: extend file-api with a service identity, sha256, JSON/X12 types and a list endpoint — larger change in a shared service. |
| X2 | **Accepted 2026-10-23.** Who receives tasks while there is one user (D21)? | A setting `operator_user_id` (the single user). Every Order Bridge task is created with `assigned_user_id` = that user and `assigned_role` = `order_bridge_operator` (a new role code, so the tasks are not broadcast to every system_admin). Notification rules (FR-OB-56) default to that user. | D21 says everything goes to that user and nothing may be routed to an unheld role. |
| X3 | **Accepted 2026-10-23.** Which mailbox sends external email (Radiant cancellation notice, retailer invoice email, failed-940 email)? | The outbound `po_smtp_*` settings in `company_config` (the account already used to send POs to suppliers), with a configurable From/Reply-To for Order Bridge. Internal notifications go through the Task Inbox outbox. | External partners should see the company's outbound address, not the internal notifier. |
| X4 | **Accepted 2026-10-23.** Public host names | `order-bridge.canadaroyalmilk.com` (front end) and `orders-api.canadaroyalmilk.com` (API), with DNS A records arranged by the user, as for Inventory. | Same pattern as every module. |

X1 is recorded in the PRD as D22: FR-OB-05 now reads "archived in the Order Bridge document store", and section 11 step 1 and the settings-key note follow.

## 2. Module, service and wiring

| Item | Value |
| --- | --- |
| Back end | `orders-api/` — FastAPI, Python 3.12, port **8014** |
| Front end | `order-bridge/` — Vite + React + TypeScript + `@uniops/shell`, port **5182** |
| Database | Shared `epms` database, `public` schema, every table prefixed **`obr_`**; Alembic version table **`alembic_version_orders`**; revision ids `obr01_…`, `obr02_…` (≤ 32 characters; `ob01_…` is already taken in epms-api) |
| Settings | `app/core/config.py` (pydantic-settings) reads `POSTGRES_*` only (as inventory-api; `DATABASE_URL` is a derived property), `JWT_SECRET_KEY` (required), `ALLOWED_ORIGINS`, `ORDERS_SCHEDULER_ENABLED` (env master switch, default false in dev), secret names in §5.3, and the two **prod-only outbound switches** of §5.5: `ORDERS_EXTERNAL_EMAIL_ENABLED` and `ORDERS_PARTNER_WRITES_ENABLED` (both default false; set to true only in `.env.prod`) |
| Auth | Identity-issued HS256 JWT verified with the shared key (`app/core/security.py`, `deps.py`), permissions through `packages/authz` (`require_permission`) |
| Health | `GET /health`, `GET /health/db` (mounted at root and under `/api/v1`, as inventory-api) |

Places a new service must be added (from the Inventory rollout): `docker-compose.dev.yml` (API block, front-end block, `order_bridge_node_modules` volume, `VITE_ORDER_BRIDGE_URL` for portal-frontend), `docker-compose.prod.yml` (`orders-api`, `order-bridge-web`, portal-web build arg), `Caddyfile` (two sites), `.env.prod.example`, `.env.lan.example`, `make-stack-env.sh` (`ORDERS_API_PORT=8014+off`, `ORDER_BRIDGE_PORT=5182+off`), `migrate-prod.sh` (`orders-api` after `identity-api`), `check-health.sh`, `reset-db.sh`, `run_tests.sh`, `DEPLOYMENT.md`.

**CORS:** add the 5182 origin (dev) and the order-bridge host (prod) to every `ALLOWED_ORIGINS`: the defaults in each `*/app/core/config.py` (12 services), the 12 inline lists in `docker-compose.dev.yml`, `.env.prod.example`, `.env.lan.example`, `epms-api/.env.example`. The front end calls epms-api (permissions, branding), identity (login hand-off) and orders-api; missing origins break branding and permissions silently.

**Portal:** `navConfig.tsx` MODULES entry `{label:'Order Bridge', href:'order-bridge', anyPermission: ORDER_BRIDGE_ACCESS_PERMS}` where `ORDER_BRIDGE_ACCESS_PERMS` lists the seven keys of §6; `resolveNavHref` → `${ORDER_BRIDGE_URL}/#__session=…`; `portal/src/lib/api.ts` `ORDER_BRIDGE_URL` and `RETURN_URL_ORIGINS`; `PortalHome.tsx` card with `hasOrderBridgeAccess`; `AdminPanel.tsx` `MODULE_TAGLINE_KEYS` gets `{key:'order_bridge', label:'Order Bridge'}`; branding via `useBranding('order_bridge')`.

## 3. Data model

All tables `obr_`. Status values are closed enumerations (PostgreSQL enum types or CHECK constraints) taken from PRD section 9.1 (FR-OB-08). Every table has `created_at`, `updated_at`; user-editable tables also `updated_by`.

### 3.1 Configuration (editable in the UI, CSV import/export — FR-OB-09)

| Table | Key / unique | Main columns |
| --- | --- | --- |
| `obr_partners` | `code` (immutable after first document) | name, abbrev, channel (SHOPIFY / TRUECOMMERCE / MANUAL), settlement_mode (PREPAID / INVOICE), sending_mode (AUTOMATIC / MANUAL), delay_minutes, invoice_method (810_JSON / EMAIL / NONE), invoice_email, invoice_cc, payment_terms, dc_routing_required, min_shelf_life_days (nullable), label_code, carrier defaults, active |
| `obr_items` | `item_code` | description, sku, upc, gtin, gross_weight_lb_per_ea, warehouse_held, active |
| `obr_item_units` | (item_code, unit) | unit (EA / CASE / 2PACK / …), factor_ea; CHECK EA = 1 (FR-OB-02) |
| `obr_item_partner_codes` | (partner_code, buyer_part) unique | item_code (FR-OB-02: buyer parts scoped per partner) |
| `obr_dc_routing` | (partner_code, ship_to_gln) unique | dc_number, notes |
| `obr_warehouses` | `code` | name, address lines, role (SHIPPER / WAREHOUSE / TRANSFER_SOURCE) |
| `obr_connections` | `name` | kind (SFTP / SHOPIFY), host, port, username, folders (inbound, outbound, processed, error, test_inbound, test_outbound), after_read (LEAVE / MOVE / DELETE), stable_seconds (default 60), mode (LIVE / TEST), active, auth_failed_at, auth_failed_reason, secret_name (name of the server secret, never the secret) |
| `obr_settings` | `key` | scheduler interval and pause, Shopify cut-off (9000), ACK timeout (120 min), stale-run threshold, paused-alarm duration, operator_user_id, email From/Reply-To, task age for health WARNING |
| `obr_notification_rules` | `event_type` | channel (TASK / EMAIL / BOTH), recipients (user ids, external addresses), active (FR-OB-56) |

### 3.2 Documents and runs

| Table | Key / unique | Main columns |
| --- | --- | --- |
| `obr_documents` | `id`; **`sha256` unique**; (connection_id, remote_name, size, remote_mtime) unique where direction = IN | direction (IN / OUT), origin (PARTNER / UNIOPS / ZOHO_REFERENCE / ZOHO_MIGRATION), system (RADIANT / TRUECOMMERCE / SHOPIFY / EMAIL), connection_id, kind (850_JSON, 940_EDI, 943_EDI, 944_EDI, 945_EDI, 997_EDI, 810_JSON, INVOICE_EMAIL, SHOPIFY_ORDER_JSON, OTHER), remote_name, size, remote_mtime, **content bytea** (X1), body_json (SQL NULL when not parsed), processing_status, file_status, ack_status, isa13 / gs06 / st02, partner_code, error, replaced_by_id, zoho_file_id (migration) |
| `obr_document_links` | (document_id, entity_type, entity_id) | many-to-many to orders and transfers |
| `obr_runs` | `id` | started_at, heartbeat_at, finished_at, status (RUNNING / SUCCEEDED / PARTIAL / FAILED), trigger (SCHEDULE / MANUAL), pass report counts per connection (FR-OB-76), error summary |
| `obr_log` | `id` | run_id, level (ERROR / WARNING / INFO), step, document_id, order_id, transfer_id, message, context jsonb (FR-OB-53) |
| `obr_audit` | `id` | user_id, action, entity, before / after jsonb, at (FR-OB-53) |
| `obr_shadow_matches` | (document_id) unique | inbound 997/945/944 received while its channel is in TEST mode, matched_reference_id (a ZOHO_REFERENCE document), what UniOps would have recorded (jsonb), difference, reviewed_by (§5.4) |

### 3.3 Orders

| Table | Key / unique | Main columns |
| --- | --- | --- |
| `obr_orders` | `id`; `order_number` from sequence `obr_order_number_seq` (starts above Zoho's highest); **(partner_code, source_order_id) unique** | source_type, source_order_number, source_reference, order_type, department, dates (order, requested delivery, do-not-deliver-after, cancel), ship-to / bill-to / contact (snapshot), currency, totals, **fulfillment_status**, **invoice_status**, hold_reason, **historical** (bool, FR-OB-17), first_received_at, **paid_at** (delay clock, D20), archived_in_shopify_at, archive_suppressed, shopify_financial_status, shopify_fulfillment_id (AC-OB-72), warehouse_order_number_received (FR-OB-28), zoho_order_id |
| `obr_order_lines` | (order_id, line_no int) unique | item_code, source item fields, ordered_qty, order_unit, factor_ea, qty_ea, unit_price, line_status (Active / Shipped / Short Shipped / Cancelled / Error), shipped_ea, delivered_qty, removed |
| `obr_940s` | `id`; control_number unique | order_id, document_id, status (GENERATED / SENT / ACCEPTED / ACCEPTED_BY_945 / REJECTED / TIMEOUT / FAILED / REPLACED / CANCELLED), sent_at, ack_document_id, ak5_code, **locked_values** jsonb (the item, quantity, unit, warehouse and ship-to that were sent — FR-OB-16), replaced_by_id |
| `obr_shipments` | (document_id) unique | order_id (nullable — FR-OB-25c), ship_date, carrier, mode, tracking, bol, carrier_ref |
| `obr_shipment_rows` | (shipment_id, lx_index, w12_index) unique (FR-OB-25) | sscc (text), item_code, lot, expiry, qty_shipped_ea, qty_ordered_ea, weight, order_line_id (nullable) |
| `obr_delivery_confirmations` | (order_id) unique | confirmed_by, confirmed_date, per-line delivered_qty (FR-OB-31) |
| `obr_invoices` | `invoice_number` (`IN-<order number>`) | order_id, method, invoice_date, totals, status, document_id, edited_by / edited_at (FR-OB-35) |
| `obr_invoice_lines` | (invoice_id, line_no) | qty (case UOM), unit_price, amount |

`obr_x12_control_seq` — one PostgreSQL sequence for all outbound X12 (D10), started above Zoho's highest control number at cutover.

### 3.4 Transfers

| Table | Key / unique | Main columns |
| --- | --- | --- |
| `obr_transfers` | `transfer_number` (5-digit, from Zoho's next) | from / to warehouse, status (Draft … Discrepancy, Failed, Cancelled, On Hold), created_by |
| `obr_transfer_lines` | (transfer_id, line_no) | item_code, requested_ea, received_ea, line_status |
| `obr_receipts` | (document_id) unique | transfer_id, receipt_date, applied (bool — false for a second, different 944, FR-OB-83) |
| `obr_receipt_rows` | (receipt_id, lx_index) unique | item_code, upc, lot, qty_ea, transfer_line_id (nullable), discrepancy_reason |

### 3.5 Tasks

Tasks are rows in the shared `tasks` table (owned by epms-api), written directly as finance-api does (`ap_duplicate_invoice_tasks.py`): `type` = `ob_<event>` (≤ 40 chars), `document_type` = `order_bridge` (≤ 20), `document_number` = a **problem key** (`OB:<event>:<entity id>`), `assigned_user_id` = operator (X2), `notified_at` NULL so the Task Inbox outbox emails it when the policy is on. Before inserting, the service looks for an open task with the same `document_type` + `document_number` and refreshes it instead (one open task per problem, FR-OB-06); when the condition clears, the task is completed with `completed_at` (e.g. archived-order task when the order is un-archived — FR-OB-03d). Task writes run in a savepoint so a task failure never fails a run.

### 3.6 Migrations

| Service | Revision | Content |
| --- | --- | --- |
| orders-api | `obr01_config` | partners, items, units, partner codes, DC routing, warehouses, connections, settings, notification rules, enums |
| orders-api | `obr02_documents_runs` | documents, links, runs, log, audit |
| orders-api | `obr03_orders` | orders, lines, 940s, shipments, confirmations, invoices, sequences |
| orders-api | `obr04_transfers` | transfers, lines, receipts, rows |
| identity-api | `0023_order_bridge_perms` | the seven `order_bridge.*` keys in `permission_defs` (module `order_bridge`), granted to `system_admin`; role `order_bridge_operator` in `role_defs` with all seven (D21) |

Before writing each migration: confirm single head per service and the parent (identity head today: `0022_inventory_perms`; the EHS branch carries its own identity 0016–0020 — coordinate if it merges first), and run the ≤ 32-character check. No seed data in migrations (FR-OB-09): configuration arrives by CSV import.

## 4. Processing

### 4.1 Scheduler

- orders-api gets `app/core/background.py` with the same `spawn(coro, name=)` / `drain(timeout)` contract as epms-api (strong references, exceptions logged, cancel-and-await on shutdown). The scheduler loop is started with `spawn` in the lifespan and stopped by `drain(timeout=10)` **before** `engine.dispose()`; no bare `asyncio.create_task`.
- Loop: fixed tick (30 s); each tick re-reads `interval` and `paused` from `obr_settings` (FR-OB-07; default 5 min; interval change takes effect on the next tick without restart) and runs a cycle when due.
- Single flight: a transaction-scoped advisory lock (`pg_try_advisory_xact_lock(<orders key>)`) around claiming the run, plus an `obr_runs` row in RUNNING with a heartbeat. A run whose heartbeat is older than the stale threshold is closed as FAILED by the next cycle (FR-OB-07, AC-OB-46).
- "Run now" in the UI (permission `order_bridge.file.retry`) claims a run through the same path; it never runs a second cycle concurrently.
- Env switch `ORDERS_SCHEDULER_ENABLED` must be true for the loop to start (false in dev and test stacks by default, so a developer stack never talks to partners).

### 4.2 One cycle

Steps run in order; each unit of work (one connection, one document, one order) runs in its own transaction and `try` block, records its outcome in `obr_log`, raises its task when needed and moves on (FR-OB-50, AC-OB-47):

1. **Fetch** every active connection (SFTP: list → filter → download; Shopify: pages from the cursor). Store each document (FR-OB-70..76, §4.3).
2. **Parse and apply inbound** documents in arrival order: 850 → orders; Shopify pages → orders; 997 → 940/943 ACK; 945 → shipments; 944 → receipts.
3. **Time-based rules:** payment-hold release (FR-OB-12), ACK timeouts (FR-OB-23), worklist entry for delivery confirmation (FR-OB-31), archived-order checks (FR-OB-03d).
4. **Build outbound:** 940s for eligible orders (FR-OB-03d), 943s for Ready To Send transfers, 810 JSON / invoice emails for confirmed orders. Each file is validated (FR-OB-29a) and stored before sending.
5. **Send** outbound documents in TEST or LIVE folders per connection mode (§5.4).
6. **Write back to Shopify:** fulfillment with tracking at the 945 (FR-OB-11, D16), archive the order (FR-OB-11a), add tracking to a Zoho-created fulfillment for migrated orders (AC-OB-72).
7. Close the run with its pass report.

**Authentication-failure latch:** a refused login sets `auth_failed_at` on the connection; later cycles skip that connection and report it on the health page until a user clears it (FR-OB-50, AC-OB-48). Network errors are not latched; they are retried on the next cycle.

### 4.3 Intake pipeline (FR-OB-70..76)

For each listed remote file: skip if (connection, name, size, mtime) is already stored → skip if younger than `stable_seconds` (too new) → download → compute sha256 of the original bytes → insert `obr_documents` (a unique violation on sha256 records a duplicate) → **commit** → apply the after-read action (Leave / Move / Delete; failures only logged) → classify (JSON top-level key; X12 by ST01; our own 940/943 reflected in Radiant's folder = not ours, counted and not stored). Unparseable content is still stored and raises a task. Text is decoded tolerant UTF-8. The pass report counts listed / too new / already here / not ours / stored / duplicates / failed, and listed must equal the sum (AC-OB-09).

### 4.4 Flows

| Flow | Design |
| --- | --- |
| Shopify pull | Admin REST `orders.json?status=any&updated_at_min=<cursor>&order=updated_at asc`, paginated (page_info links), without `customer` fields; inclusive cursor advanced to the batch maximum after commit (FR-OB-10). Each page is stored as a SHOPIFY_ORDER_JSON document. Orders upsert on (partner, Shopify id); below the cut-off → historical. Lines: shippable product lines only, current quantity (FR-OB-11b); unit from Item Master (FR-OB-02); paid_at set when payment becomes satisfied (D20). |
| 850 | Parse every PO in the file (FR-OB-13); partner from `PurchaseOrder[0].TradingPartner`; lines matched GTIN → UPC → partner buyer part; case pack checked against Item Master (FR-OB-02); unmatched → order in Review + task (FR-OB-15); resend after 940 → order updated, 940 values stay locked, task (FR-OB-16). |
| 940 | Eligibility is one SQL predicate plus a Python function that implements FR-OB-03d exactly (both are tested against the same cases — AC-OB-21/22). Builder renders Appendix A.3 from a template per channel; control number from `obr_x12_control_seq`; validator checks mandatory segments, EA quantities, DC rule, country default (FR-OB-29/29a). Send 940 now and replacement resend reuse the same builder (FR-OB-03c). |
| 997 | Parse AK1/AK2/AK5/AK9; match on partner + group + GS06 + ST02, zeros normalised (FR-OB-22a); accepted → 940 Accepted; rejected → 940 Rejected + task with AK5 code; also for 943 (FR-OB-82). |
| 945 | One per order (D18): parse every LX / W12 into shipment rows (FR-OB-25b), order key W06-02/06 with one-letter strip (FR-OB-28), ship date DTM else G62, idempotent by document hash and row position, closes the fulfilment, shortfall task (FR-OB-25), second different 945 stored not applied + On Hold (FR-OB-25), validation notifications (FR-OB-25d), no matching order → stored + "create order" task (FR-OB-25c). |
| 943 / 944 | 943 per Appendix A.4 (NTE, W27, W03 as in the sample); 944 one per transfer, W07 rows summed per line + lot, total checked against W14, allocation and Discrepancy per A.7 / FR-OB-84, second different 944 stored not applied + On Hold (FR-OB-83). |
| Invoicing | Worklist when shipped **and** requested date reached (FR-OB-31); bulk confirmation with delivered quantities; then by partner method: 810 JSON (Appendix A.9) uploaded to TrueCommerce `Import`, or email with invoice / PO / packing-list PDFs (reportlab, as in epms-api `pdf_*`), or none (FR-OB-30..35). Invoice edits audited and resendable. |

### 4.5 Status changes

All status changes go through one module (`app/domain/status.py`, which declares the four PRD 9.1 machines on the platform `StateMachine`, §13.6) that enforces the allowed transitions of PRD section 9.1 and writes `obr_audit` for user actions. Order state is computed (SQL view `obr_order_state_v`), never stored (D17). Unit tests enumerate the transition table.

## 5. Integrations

### 5.1 SFTP

`asyncssh` client (new dependency; no SFTP code exists in the repo). Direct connection to `commhub.wheelsgroup.com` and `sftp.truecommerce.com` (FR-OB-04, no ftp-api.com). Host keys pinned in configuration (first-connect fingerprint shown to the admin for confirmation). Connection test lists folders only (FR-OB-74).

### 5.2 Shopify

`httpx` client with the client-credentials grant; the 24-hour token is cached in memory and renewed 600 s before expiry. App scopes needed: read_orders, write_orders (archive), read/write_fulfillments, read_merchant_managed_fulfillment_orders; not read_customers. API version from the connection (2026-04 today).

### 5.3 Secrets (FR-OB-04)

SFTP passwords (or keys) and the Shopify client id/secret live only in the server environment (`.env.prod`, injected as env vars named in `obr_connections.secret_name`, e.g. `OBR_SECRET_RADIANT_SFTP`). The database and the UI never hold them; the UI shows "secret present / missing" only. Rotation at Zoho retirement (section 11 step 5) is an env change plus restart.

### 5.4 Test vs live (shadow run)

Each connection has `mode` LIVE or TEST. During the shadow run: all inbound after-read policies are Leave (Zoho must still see the files), outbound goes to the test folders (TEST mode) or is generated without sending, Shopify write-back is disabled by a setting, and the comparison job (§10) diffs UniOps 940s/810s with Zoho's archived files. Switching a channel to LIVE is a configuration change logged in `obr_audit`; the go-live gate in PRD section 11 (notification recipients + test notification, AC-OB-71) is checked by the health page before the switch is allowed.

**Acknowledgements and confirmations in shadow mode.** While only Zoho sends, every real 997 Radiant returns acknowledges a Zoho 940/943 with Zoho's control numbers, and every real 945/944 answers a Zoho 940/943. Applied normally, FR-OB-22a ("no match → review + task") would raise a task for every real 997 for weeks, and 945s/944s would be applied to orders whose 940 UniOps never sent. Design:

- A channel in TEST mode keeps a **reference copy of what Zoho sent**: Zoho's outbound 940/943 files are read continuously (from Radiant's inbound folder with policy Leave, or from the Zoho file-register export if Radiant moves them away) and stored in `obr_documents` with `origin = ZOHO_REFERENCE`. They are never sent and never count as a live 940.
- Inbound 997/945/944 for a TEST-mode channel are stored and matched **against the reference copies** (same rules as FR-OB-22a/28/83). The result is written to a comparison table (`obr_shadow_matches`: document, matched reference, what UniOps would have done, difference) and shown on a shadow-run page. In TEST mode these documents **do not change order or transfer status and raise no review tasks**. Only parse failures and intake errors still raise tasks, because they would also be real problems after cutover.
- The comparison job then shows, per order: Zoho's 940 vs the 940 UniOps built; the 997/945/944 that came back; and what UniOps would have recorded. Exit criterion (a) of PRD section 11 step 2 is evaluated on this table.
- If UniOps writes its own 940s to Radiant's `/test/in`, their 997s (if Radiant returns any) arrive in `/test/out` with UniOps' control numbers and are matched normally against UniOps' own TEST 940s.
- **Needs Radiant's confirmation:** whether files in `/test/in` are processed at all, whether they produce 997s (and 945s) in `/test/out`, and that they never ship goods. PRD section 11 step 2 (a) assumes "a 997 and a 945 come back"; if Radiant's test side returns nothing, criterion (a) can only be met from the reference matches above, and the first real UniOps 940/997/945 round trip happens at the per-channel switch (hypercare).

### 5.5 Dev and test never reach partners

The dev server snapshot contains the real production SMTP credentials, and connection rows can be imported from production. So reaching a partner is gated by **environment switches that exist only in `.env.prod`**, independent of any connection's mode or any database setting:

| Action | Allowed only when | Otherwise |
| --- | --- | --- |
| Email to any address outside the company domain(s) (Radiant, retailers) | `ORDERS_EXTERNAL_EMAIL_ENABLED=true` | not sent; logged in `obr_log` with recipients, subject and body hash ("would have sent") |
| SFTP `put` to a LIVE folder | `ORDERS_PARTNER_WRITES_ENABLED=true` **and** connection mode LIVE | not written; logged as "would have written" |
| SFTP after-read Move / Delete on a partner server | `ORDERS_PARTNER_WRITES_ENABLED=true` | treated as Leave |
| Any Shopify write (fulfillment, tracking, archive) | `ORDERS_PARTNER_WRITES_ENABLED=true` | not called; logged |

Reads (SFTP list/get, Shopify GET) stay possible in dev when credentials are deliberately provided, so replay and connection tests work. The checks sit in the lowest-level clients (`email.send_external`, `sftp.put/move/delete`, `shopify.post/put`), so no flow can bypass them, and the health page shows both switches. Internal email to company addresses goes through the Task Inbox outbox as today.

## 6. API and permissions

Permissions (identity migration `0023_order_bridge_perms`; module `order_bridge`; front ends read them from epms-api `/config/me/permissions`):

| Key | Used by |
| --- | --- |
| `order_bridge.read` | all GET endpoints, all pages |
| `order_bridge.order.operate` | hold release, Send 940 now, delivery confirmation, manual orders, review resolution, record cancellation/return, cancel |
| `order_bridge.file.retry` | resend / reparse / ignore documents, run now |
| `order_bridge.transfer.manage` | create, send, cancel transfers; review receipts |
| `order_bridge.inventory.adjust` | reserved for the next version (no endpoint in v1) |
| `order_bridge.invoice.manage` | edit, send, resend invoices |
| `order_bridge.config` | partners, items, DC routing, connections, settings, notifications, CSV import/export |

D21: at go-live the operator's role (`order_bridge_operator`) holds all seven; adding people later is an Access Control Matrix assignment only.

Endpoint groups under `/api/v1` (all JSON; list endpoints paged with `page`, `page_size` ≤ 200, `sort` from a whitelist, `total`):

- `orders` (list, detail, lines, documents, timeline), `orders/{id}/send-940`, `/release-hold`, `/cancel`, `/record-return`, `/resolve-review`, `orders/manual` (Draft, submit)
- `worklists/review`, `worklists/delivery-confirmation` (+ bulk confirm), `worklists/action-required` (reads the shared tasks for `document_type = order_bridge`)
- `shipments`, `invoices` (edit, send, resend, PDF), `transfers` (+ send, cancel), `receipts`
- `documents` (list, detail, raw download, reparse, resend, ignore)
- `runs` (list, detail, run now), `health`, `dashboard`
- `config/partners|items|item-units|partner-codes|dc-routing|warehouses|connections|settings|notification-rules` with `GET …/export.csv` and `POST …/import.csv` (validate-all-then-write, dry-run preview, rejects unknown columns; FR-OB-09)

## 7. Front end

`order-bridge/` follows `inventory/` (session hand-off from the Portal hash, `@uniops/shell` tabs, TanStack Query, permissions from epms-api, branding). Unlike Inventory, **each nav item is gated by its permission and the landing page is the first visible item** (the Finance pattern), because operators and readers see different sets.

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
| Configuration | partners, items and units, partner codes, DC routing, warehouses, connections (secret present/missing, test), settings, notification rules; CSV import/export on each | config |

Reachability is part of done for every page: Portal card and sidebar entry visible with any of the seven keys; module nav shows only permitted pages; a user with none sees the "no access" screen; Task Inbox links (`HREF_MAP` / `taskHref` in epms and Portal) open the right Order Bridge page.

## 8. Notifications

- Event types are the FR-OB-56 list (plus 945 validation, paid order archived, transfer discrepancy). Each event maps to a task type `ob_<event>`; rules in `obr_notification_rules` choose TASK / EMAIL / BOTH and recipients (default: the operator, X2).
- Internal: tasks in the shared `tasks` table; the epms-api task notifier sends the email when the type's policy is on. New task types default to email OFF in `task_notify_policy.DEFAULT_IMMEDIATE`, so the go-live gate includes turning them on in Portal → Admin → Task Notification Policies.
- Registrations needed outside orders-api: `epms/src/lib/taskTypes.ts` (`TASK_TYPE_LABELS`, `HREF_MAP` / `taskHref` for `document_type = order_bridge`), `epms/src/services/tasks.ts` (`TaskType`), `portal/src/pages/PortalHome.tsx` (labels, module mapping, heading), and `epms-api/app/crud/task.py` `_TASK_NO_STATUS_INVARIANT` (external types). The Portal deduplicates inbox rows by `document_number`, which is why problem keys are unique per problem.
- External emails (Radiant cancellation, retailer invoice) are sent by orders-api through an aiosmtplib helper with the X3 account. Every user-supplied value inserted into an HTML email is passed through `html.escape` (the `_render(…, escape=True)` pattern of epms-api); templates are not user-editable in v1.

## 9. Migration from Zoho

1. **Configuration:** export partners, items, DC routing (457 rows), warehouses and settings from Zoho as CSV; import through the configuration screens (dry run first). Fix the known data issues at import (CF0110 UPC, Jean Coutu invoice email, Overwaitea).
2. **History:** a one-off importer (`app/scripts/import_zoho.py`, run inside the orders-api container against an export folder) loads orders, lines, lots, transfers and the file register; raw files from WorkDrive are loaded into `obr_documents` with their sha256. Status values are mapped by the PRD 9.1 table. Orders shipped, completed or cancelled in Zoho, and Shopify orders below the cut-off, get `historical = true`.
3. **In flight:** orders without a 940 or waiting for 997/945 keep their mapped status; for Shopify orders the Zoho-created fulfillment id is stored (`shopify_fulfillment_id`, AC-OB-72).
4. **Counters:** `obr_order_number_seq`, `obr_x12_control_seq` and the transfer number start above Zoho's highest; the Shopify cursor is handed over at the switch.
5. **Checks:** row counts per table, totals per status, and the full-history dry run (AC-OB-21) before any send is enabled.

## 10. Testing

- **Where:** tests run only in throwaway containers on the dev network with a `*_test` database (the orders-api conftest refuses any database name not ending in `_test`, as inventory-api does) — never with the host `.env`, which points at production. Schema built by `alembic upgrade head` per test session.
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
| P0a Platform, UI library, template | `tools/new-module`, `tools/check-scope.sh`; the bare `orders-api` and `order-bridge` skeletons generated by `tools/new-module --platform=inplace` (product owner, 2026-10-27), registered nowhere — no compose, Caddy, Portal, identity or CORS change; `orders-api/app/platform` (§13.1–13.14) and `order-bridge/src/platform-ui` (§13.15) written into them, each with README, tests and the one-way-dependency check; the four 9.1 machines in `orders-api/app/domain/status.py` | platform unit tests green (state machine vs 9.1, exclude_unset clear/untouched, CSV validate-all, guard decorator negative + positive, outbox retry policy); UI library builds and typechecks in order-bridge · scope check + regression gate pass | 7–9 |
| P0 Wiring | wiring of the P0a skeletons from the template's printed checklist: compose/Caddy/env/CORS/Portal, identity permissions migration, health, `obr01`, guide-layer module entry | module reachable from the Portal with the seven keys; health green; guide test passes · scope check + regression gate pass | 2 |
| P1 Configuration | config entities on `CrudService` / `CrudPage`, CSV import/export, connections with secret status and connection test | full Zoho configuration imported in dry run and for real on dev · scope check + regression gate pass | 2 |
| P2 Scheduler and intake | `obr02`, scheduled jobs (cycle, outbox), SFTP gateway, intake pipeline, documents page, runs/logs | AC-OB-01…09, 46…48 pass against the SFTP container · scope check + regression gate pass | 5 |
| P3 Shopify inbound | Shopify gateway, order upsert, units, payment hold, delay clock, archive rules, review worklist, manual orders | AC-OB-10, 12…20, 22, 23, 70, 74, 79 · scope check + regression gate pass | 5 |
| P4 940 / 997 | eligibility, builders, validator, control sequence, sending, 997 parsing, timeout, Send 940 now, 850 inbound | AC-OB-11, 21 (on dev data), 24…33, 62, 63; segment-by-segment replay of archived 940s · scope check + regression gate pass | 6 |
| P5 945 and Shopify write-back | 945 parser, shipments and lots, short shipment, second 945, validation checks, fulfillment + tracking + archive, migrated fulfillments | AC-OB-34…39, 56…61, 72, 73, 77, 78 · scope check + regression gate pass | 5 |
| P6 Invoicing | delivery-confirmation worklist, invoice senders (810 JSON, email with PDFs, none), invoice edit, cancellation/return | AC-OB-40…45 · scope check + regression gate pass | 5 |
| P7 Transfers | 943 builder, 944 parser, allocation, discrepancies, transfer pages | AC-OB-64…69, 75, 76 · scope check + regression gate pass | 3 |
| P8 Health, dashboard, notifications | health checks and gate, dashboard, notification rules, task types registered in epms/Portal | AC-OB-49…55, 71; reachability check · scope check + regression gate pass | 3 |
| P9 Zoho migration | importer, status map, historical flag, counters, in-flight fulfillments, row-count checks | full import on a copy; AC-OB-21 dry run agreed by a person · scope check + regression gate pass | 4 |
| P10 Shadow run and cutover | comparison job, test folders, channel switches, runbook | PRD section 11 exit criteria met; per-channel switch · scope check + regression gate pass | 2 build + calendar time |

Total ≈ 49–51 build days (P0a at 7–9), plus the shadow-run calendar period.

**Guide layer:** P0 adds an `order_bridge` entry to `epms-api/app/knowledge/modules.yaml` (key, label, covers, who, typical, notes — prose only, no `documents`/`reports`) and to the presence check in `tests/test_module_guide.py`; every later phase that changes what a user can do updates that entry in the same change (feedback rule). Status names and rules are not copied into the YAML; if the assistant must answer them later, they are derived from the orders-api status module.

## 12. Risks and open points

**Open PRD items (unchanged):** OB-OI-02 (inventory recalculation history — next version), OB-OI-04 (Zoho completed count of 8,000 — affects the migration row-count check), OB-OI-07 (who releases paid Shopify orders today — affects the shadow-run comparison of held orders), OB-OI-30 (credit note and inventory handling for returns — FR-OB-19 records only), OB-OI-38 (Radiant data completion — next version).

**Found while designing (need an answer, not invented here):**

1. **X1–X4** — accepted 2026-10-23 (§1; X1 recorded in the PRD as D22). DNS records for the two host names are arranged by the user.
2. **Shadow-run file access:** Zoho reads Radiant and TrueCommerce through ftp-api.com; if Zoho's `*_DELETE_AFTER_DOWNLOAD` is on, or Radiant moves files after Zoho reads them, UniOps will not see the same files. Confirm both settings before P10.
3. **Radiant test folders and shadow-mode acknowledgements (§5.4):** confirm with Radiant whether `/test/in` is processed, whether it returns 997s and 945s in `/test/out`, and that it never ships. Without test-side responses, PRD section 11 step 2 (a) is met only through the Zoho reference matching, and the first real UniOps round trip happens at the channel switch. Also confirm how Zoho's sent 940/943 can be read continuously (Radiant inbound folder with Leave, or a recurring export of the Zoho file register).
4. **Dev reaching partners (§5.5):** the dev snapshot holds real production SMTP credentials; the two prod-only switches must stay false outside `.env.prod`, and the release checklist verifies them.
5. **Shopify custom app:** the client-credentials app and its scopes (§5.2) must exist on the store; confirm who creates it.
6. **Task notification default OFF:** new task types send no email until enabled in Task Notification Policies — part of the go-live gate, easy to miss.
7. **Zoho export completeness:** the importer needs the export of lots, file register and WorkDrive files with their original bytes; confirm the export path before P9.

## 13. Platform foundation (Order Bridge only)

Approved 2026-10-23: an ABP-inspired foundation in the back end and a UI library in the front end, **used only by Order Bridge first** (isolation rules in §14). Product owner, 2026-10-27: keep both inside this project for now, each in its own folder so it is easy to separate later.

| Part | Folder | Used by |
| --- | --- | --- |
| Back-end foundation | `orders-api/app/platform/` | `orders-api/app/domain/…`, `app/flows/…`, `app/api/…` |
| UI library | `order-bridge/src/platform-ui/` | `order-bridge/src/pages/…`, `src/features/…` |

**Extraction-ready rules (both folders):**

- **One-way dependency:** the platform folder never imports business code. Back end: a test walks the AST of every module under `app/platform/` and fails on any import of `app.` outside `app.platform` (import-linter style, no new dependency). Front end: an ESLint `no-restricted-imports` rule for `src/platform-ui/**` forbids `@/…` paths outside `@/platform-ui`; if the module ships without ESLint, the same check runs as a vitest test that scans the import statements.
- **No Order Bridge names** inside the platform code: no partner, order, 940 or Radiant identifiers. Domain enums, status maps and permission keys are passed in by the business code (e.g. `StatusBadge` takes a map; `useCan` takes a key).
- **Own README and tests:** `app/platform/README.md` and `src/platform-ui/README.md` describe the public interface; tests live next to them (`tests/platform/…`, `src/platform-ui/**/*.test.tsx`).
- **Own dependency list documented:** each README lists the third-party packages the folder needs (back end: SQLAlchemy, pydantic, asyncssh, httpx, aiosmtplib; UI: Radix, `@tanstack/react-table`, `react-hook-form`, `zod`), so extraction knows exactly what moves.
- **Extraction later** = move the folder, add its own package manifest, change imports. No code inside it changes.

**Platform tables (interpretation agreed 2026-10-27):** the tables the platform works with — audit, outbox, settings, runs, log, X12 control sequence — are named `obr_audit`, `obr_outbox`, `obr_settings`, `obr_runs`, `obr_log`, `obr_x12_control_seq` in this document, but no such name appears in `app/platform/`. The platform ships the column shapes as mixins and factories; `orders-api/app/models` supplies the table names (and the sequence name) when it declares the concrete models, and the tables are created by `obr01` (P0) and `obr02` (P2). Platform tests declare their own test tables (`create_all` in the `orders_test` database).

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

- Config tables referenced by orders (`obr_partners`, `obr_items`, `obr_item_units`, `obr_dc_routing`, `obr_warehouses`, `obr_connections`) are `Audited + Versioned + Deactivatable` and have **no delete** in the repository or the API.
- Documents (`obr_documents`) are `CreationAudited` only and have **no delete and no update of content** (FR-OB-05); status columns change only through the state machine.
- `SoftDeletable` is **available, not used in v1**: the platform provides the mixin with its automatic filter (§13.3) and helpers for partial unique indexes (`unique … WHERE deleted_at IS NULL`); each entity opts in. No Order Bridge entity uses it in v1 — drafts follow PRD 9.1 (Draft → Cancelled), config tables use `active`, documents are never deleted.

Example config entity:

```python
class Partner(Audited, Versioned, Deactivatable):
    __tablename__ = "obr_partners"
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
        """Raises MissingConversion when the item has no factor for `unit`.
        Never defaults to 1: Radiant ships EA exactly as told (PRD FR-OB-02/14).
        The caller turns MissingConversion into Review + task (FR-OB-15)."""

@dataclass(frozen=True)
class Money:
    amount: Decimal                          # quantised to the currency's minor unit
    currency: str                            # CAD | USD
    def __add__(self, other: "Money") -> "Money": ...   # refuses mixed currencies
```

### 13.3 Repository and query specification

```python
class QuerySpec(BaseModel):
    page: int = 1
    page_size: conint(le=200) = 50
    sort: list[SortKey] = []                 # multi-column; each key whitelisted
    search: str | None = None                # over the resource's declared search fields
    filters: list[Filter] = []               # field + op (eq | in | contains | range), whitelisted
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

Automatic filters in `base_query`: soft-deleted rows are excluded for entities that opt in to `SoftDeletable` (none in v1); on **open-work lists** (dashboard cards, worklists, Warehouse Waiting, Invoice Work) historical orders are excluded (FR-OB-52b); elsewhere they are included only with `include_historical=true`. Unknown sort or filter keys are a `BusinessError("query.unknown_field")`, never passed to SQL.

### 13.4 Unit of work

```python
class UnitOfWork(Protocol):
    session: AsyncSession
    events: EventBuffer                      # §13.7
    async def __aenter__(self) -> "UnitOfWork": ...
    async def commit(self) -> None: ...      # dispatch in-transaction handlers, then COMMIT
    async def __aexit__(self, *exc) -> None: ...   # rollback on error
```

One unit of work per HTTP request (FastAPI dependency) and one per scheduler unit (one connection, one document, one order — §4.2).

### 13.5 Services (template method)

```python
class ReadOnlyService(Generic[T, Read]):
    repo: Repository[T]
    read_schema: type[Read]
    permission: str = "order_bridge.read"
    async def get(self, id: int) -> Read: ...
    async def list(self, spec: QuerySpec) -> Page[Read]: ...

class CrudService(ReadOnlyService[T, Read], Generic[T, Read, Create, Update]):
    permission: str = "order_bridge.config"  # every config CRUD uses the coarse key
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
- `import_csv` parses and validates **every** row first; any error returns the full error list and writes nothing; otherwise the whole file is written in one transaction. `dry_run=true` returns per-row before / after without writing (FR-OB-09).
- Permissions are the PRD's seven coarse keys only (§6); there are no per-operation keys.

```python
class CommandService(Generic[T]):
    """State changes on an aggregate. Orders are never edited by a generic PUT."""
    async def execute(self, cmd: Command) -> Result:
        async with self.uow:
            agg = await self.repo.get(cmd.aggregate_id, for_update=True)   # SELECT … FOR UPDATE
            self.check(agg, cmd)                                           # re-check the rule
            self.machine.transition(agg, cmd.target, actor=cmd.actor)      # §13.6
            self.audit(agg, cmd)
            await self.uow.commit()
```

Example command: `SendNow940(order_id, confirm_replacement: bool)` — locks the order, re-checks FR-OB-03c (no live 940 unless a confirmed replacement; never for historical orders), transitions fulfillment `Pending → 940 Generated`, raises `Order940Requested`, audits the user.

Example config resource (whole wiring for one table):

```python
class PartnerService(CrudService[Partner, PartnerRead, PartnerCreate, PartnerUpdate]):
    repo_cls = PartnerRepository             # search_fields=("code", "name")
    async def validate_create(self, data):
        if data.invoice_method == InvoiceMethod.EMAIL and not data.invoice_email:
            raise BusinessError("partner.invoice_email_required")   # FR-OB-33

router = crud_router("/config/partners", PartnerService)   # list/get/create/update/deactivate/export/import
```

### 13.6 State machine

```python
class StateMachine(Generic[S]):
    def __init__(self, transitions: Mapping[S, frozenset[S]], name: str): ...
    def can(self, src: S, dst: S) -> bool: ...
    def transition(self, entity, dst: S, *, actor: Actor, reason: str | None = None) -> None:
        """Raises BusinessError('state.invalid_transition') if dst is not allowed;
        sets the field, records old/new in the audit, raises a StatusChanged event."""
```

Four machines are declared from the PRD 9.1 tables — fulfillment, invoice, transfer, file/ACK — in business code (`app/domain/status.py`), not in the platform. A unit test enumerates every pair and compares with the PRD table. Order state stays a computed view (D17).

### 13.7 Domain events and outbox

- `EventBuffer.raise_(event)` collects events during a unit of work. **Internal handlers** (close the fulfilment, create or complete a task, recompute a worklist entry) run inside the same transaction just before COMMIT.
- **External effects** (SFTP put, Shopify call, external email) are written as rows to `obr_outbox` in the same transaction and executed by the scheduler after commit, so a crash between commit and the call loses nothing. Each row has kind, payload, attempts, last_error, status (PENDING / DONE / FAILED) and is visible on the health page.
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

Each has a real implementation (asyncssh, httpx, aiosmtplib, `obr_documents`) and a fake (in-memory, records calls) for tests. **Timing:** P0a delivers the Protocols, the in-memory fakes, `GuardedGateway` and the outbox with its per-kind retry policy (tested through fakes that raise `NotAccepted` / `AcceptedThenFailed`); the real SFTP gateway (asyncssh) comes with P2, the real Shopify gateway (httpx) with P3, the real email gateway (aiosmtplib) with P6/P8 where it is first used, and `DbDocumentStore` with `obr02` (P2). `SftpGateway` and `EmailGateway` are generic and live in `app/platform/gateways`; `ShopifyGateway` is business code (`app/integrations/shopify`). `GuardedGateway` wraps SFTP, Shopify and Email and is the **only** place that reads `ORDERS_EXTERNAL_EMAIL_ENABLED` and `ORDERS_PARTNER_WRITES_ENABLED` (§5.5): refused calls are logged as "would have sent/written" and return without effect.

### 13.10 X12 toolkit

`x12.Envelope` (ISA/GS/ST/SE/GE/IEA with the A.2 values), `x12.read(raw) -> Interchange` (separators read from the ISA, FR-OB-26), `x12.Segment` builder with element escaping, control numbers from `obr_x12_control_seq` (D10), zero-padding helpers used by both building and matching (FR-OB-22a / 28).

### 13.11 Pipeline

```python
class Step(Protocol):
    name: str
    async def run(self, ctx: CycleContext) -> StepResult: ...

async def isolated(unit_name: str, fn, *, uow_factory, log) -> UnitOutcome:
    """Runs fn in its own unit of work; on error rolls back, logs to obr_log with
    context, raises/refreshes the problem task, and returns — never re-raises
    (FR-OB-50)."""
```

The intake chain (list → identity check → stability → download → hash/store → commit → after-read → classify, FR-OB-70..76) and the cycle (fetch → apply → time rules → build → outbox → write-back) are lists of `Step`s; each unit inside a step goes through `isolated`.

### 13.12 Scheduled job

```python
class ScheduledJob(ABC):
    lock_key: int
    interval_setting: str                    # read from obr_settings each tick
    @abstractmethod
    async def run_once(self, run: RunRecord) -> RunSummary: ...
    async def tick(self) -> None:            # advisory xact lock, close stale RUNNING runs,
                                             # open run, heartbeat, run_once, close run
```

Started with `spawn()` in the lifespan, stopped with `drain()` before `engine.dispose()` (§4.1). The cycle and the outbox worker are two `ScheduledJob`s sharing the same lock discipline.

### 13.13 Cross-cutting

- **Audit:** an SQLAlchemy `before_flush` listener writes `obr_audit` for every change to an `Audited` entity (who, what, before/after); commands add their reason.
- **Actors:** `Actor(user_id, name)`; scheduler work runs as `SYSTEM_ACTOR`, recorded as such (never as a person).
- **Settings registry:** typed `Setting[T](key, default, validator)` declarations; values in `obr_settings`; read through one cached accessor.
- **Feature flags:** e.g. `shopify_writeback_enabled`, `shadow_mode` — settings with an audit trail.
- **Errors:** `BusinessError(code, message, details)` → one JSON shape `{ "error": { "code", "message", "details" } }` with HTTP 409/422; unexpected errors → 500 with a correlation id.
- **TaskService:** `raise_problem(event, entity, context)` (problem key `<prefix>:<event>:<entity>`, one open task, refresh if open), `resolve_problem(event, entity)` (auto-complete) — §3.5. The prefix (`OB`), `document_type` (`order_bridge`) and task-type names are supplied by business code; the guard decorator (§13.9) likewise receives its switch names (`ORDERS_EXTERNAL_EMAIL_ENABLED`, `ORDERS_PARTNER_WRITES_ENABLED`) from the composition root, so no Order Bridge name appears inside `app/platform/`.

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

- **Location:** `order-bridge/src/platform-ui/` (product owner, 2026-10-27; `packages/ui` and `libs/ui` are dropped). It is **not** under `packages/` and **not** in the root npm workspace, so the root `package-lock.json` never changes. It sits inside `src/` rather than beside it (`order-bridge/platform-ui/`) because there it is covered with no extra configuration by order-bridge's own `tsconfig.app.json` (typecheck), Vite (build and an `@/platform-ui` alias) and Tailwind's source scanning, so nothing outside `src/` has to be wired and nothing can silently drop out of the typecheck or the CSS. The folder rules of §13 keep it separable.
- Builds **on top of `@uniops/shell`**: reuses its Button, Badge, Card, Input, Label, FormField, Pagination, Skeleton and tokens. No second Button or any duplicate of a shell component; `packages/shell` is not modified.
- Adds: Radix-based Dialog, Sheet, Popover, Tabs, Combobox, Toast, Confirm; `DataTable<T>` on TanStack Table with a backend adapter (sort/filter/page → `QuerySpec`); `CrudPage<T>` (list + form + CSV import with dry-run preview); `SchemaForm` from zod (config forms only); `ResourceClient<T>` / `useCrudResource` working on types generated from the orders-api OpenAPI schema (the generated types live in business code, `src/api/generated`, and are passed in); `StatusBadge` taking a status → style map (the 9.1 map is supplied by business code); `ActionButton` (disabled while pending, checks a permission passed in); `PermissionGate` / `useCan`; error display by `BusinessError` code; Empty / Error states (Skeleton from the shell). Light theme only; tokens dark-ready. No TreeView.
- Build facts: `order-bridge/Dockerfile` copies `packages/shell` and `order-bridge` as inventory's does — **no extra COPY**, the library is part of `order-bridge`; `order-bridge/src/index.css` keeps only the `@source` line for `packages/shell` (the library is inside `src/` and is scanned automatically; a build check confirms its classes are present in the output CSS); Radix, `@tanstack/react-table`, `react-hook-form` and `zod` are pinned in `order-bridge/package.json` only; typecheck runs with order-bridge's own `tsc` and `tsconfig.app.json` (TypeScript ~6.0), and the check verifies that a deliberate type error inside `src/platform-ui` is reported (guards against the TS5101 "diagnostics skipped" trap). Found in P0a (2026-10-27): `@hookform/resolvers` is not used — its optional peer `ajv@8` leaves a lock file npm 10's `npm ci` rejects — so the library has a small zod resolver of its own; vitest is held at `~4.0.18` because every 4.1.x crashes npm 10 (the npm of `node:20`) while resolving peers, and 4.0 runs on its own nested vite 7, so `vitest.config.ts` has no React plugin (automatic JSX only).

### 13.16 Module template

`tools/new-module/` generates a service skeleton (FastAPI app, config, health, alembic with its own version table, Dockerfile, tests/conftest refusing non-`_test` databases) and a front-end skeleton (session hand-off, shell tabs, permission-gated nav, branding, ESLint), and prints the wiring list of §2 / §14.2 as a checklist with exact snippets. It writes only the two new folders and never edits an existing file.

**The template contains no platform code** (product owner, 2026-10-27). `--platform=none|inplace` decides what the skeleton gets:

- `none` — the default, and the only value allowed for any other module until the §14.7 decision: the skeleton has no platform imports.
- `inplace` — for `orders-api` / `order-bridge` only (any other target is refused with an error): the skeleton imports `app.platform` / `@/platform-ui`, and the platform folders are written into it by hand (P0a). Nothing is ever copied.

P0a generates the bare `orders-api` and `order-bridge` skeletons with it, so the template is proven before P0 wires them.

### 13.17 Decisions on the platform design (product owner, 2026-10-27 — all closed)

1. **Retries:** SFTP files and Shopify writes are sent once, then Failed + task + manual retry; external email retries automatically only when the SMTP server did not accept the message (§13.7).
2. **Soft delete:** the platform provides `SoftDeletable`; it is available but not used in v1 (§13.1). Drafts follow PRD 9.1 (Draft → Cancelled).
3. **Library location:** the UI library is `order-bridge/src/platform-ui/` and the back-end foundation is `orders-api/app/platform/`, both under the extraction-ready rules of §13 (§13.15).

## 14. Isolation from other projects

The product owner's condition: the foundation and the UI library are used only by Order Bridge first, and nothing may cause issues for other projects.

### 14.1 Code allow-list

New code only in `orders-api/` (including `app/platform/`), `order-bridge/` (including `src/platform-ui/`), and `tools/new-module/` (plus `tools/check-scope.sh`). No code change in any other service, front end, `packages/shell` or `packages/authz`. Front-end dependencies of the UI library are declared in `order-bridge/package.json` only; nothing is added under `packages/` or to the root npm workspace.

### 14.2 Configuration allow-list (additions only)

| File(s) | Addition |
| --- | --- |
| `*/app/core/config.py` of the 12 existing services | the Order Bridge origin in the `ALLOWED_ORIGINS` default |
| `docker-compose.dev.yml` | `orders-api`, `order-bridge-frontend`, `order_bridge_node_modules`, the origin in the 12 inline lists, `VITE_ORDER_BRIDGE_URL` for portal-frontend |
| `docker-compose.prod.yml` | `orders-api`, `order-bridge-web`, portal-web build arg |
| `Caddyfile` | two sites |
| `.env.prod.example`, `.env.lan.example`, `epms-api/.env.example` | URLs, origin, the two prod-only switches (false) |
| `make-stack-env.sh`, `migrate-prod.sh`, `check-health.sh`, `reset-db.sh`, `run_tests.sh`, `DEPLOYMENT.md` | one entry each |
| `portal/src/components/layout/navConfig.tsx`, `portal/src/pages/PortalHome.tsx`, `portal/src/lib/api.ts`, `portal/src/pages/admin/AdminPanel.tsx` | nav entry, card, URL / return origin, tagline key |
| `identity-api/alembic/versions/0023_order_bridge_perms.py` | new migration (keys, role, grants) |
| `epms/src/lib/taskTypes.ts`, `epms/src/services/tasks.ts`, `portal/src/pages/PortalHome.tsx` (labels), `epms-api/app/crud/task.py` (`_TASK_NO_STATUS_INVARIANT`) | Order Bridge task types |
| `epms-api/app/knowledge/modules.yaml`, `epms-api/tests/test_module_guide.py` | guide-layer entry and its presence check |

### 14.3 Scope check

`tools/check-scope.sh` compares `git diff --name-only <base>...HEAD` with the two allow-lists and fails on any other path; for allow-listed config files it also fails if a hunk removes or modifies an existing line (additions only). It fails on any changed lock file other than `order-bridge/package-lock.json` — including the root `package-lock.json` — and on any new folder under `packages/`. It also runs the two one-way-dependency checks of §13 (platform folders must not import business code).

### 14.4 Regression gate (before every phase commit)

1. Build and typecheck all 8 existing front ends (epms, portal, oa, vms, finance, booking, mrp, inventory) before and after the change, each with its own `tsc` and `tsconfig.app.json`; results must be identical.
2. Test suites of every touched existing service in throwaway `*_test` containers: identity-api (migration 0023), epms-api (task-type registration and the guide test).
3. Alembic heads unchanged for every service except identity-api (+1) and the new orders-api.
4. Headless Portal check: every existing module card and nav entry still appears for an admin; Order Bridge is invisible to a user without any of its seven permissions and visible with one.

### 14.5 Runtime isolation

Separate containers (`orders-api`, `order-bridge-web`); own `obr_` tables and `alembic_version_orders`; scheduler off by default (`ORDERS_SCHEDULER_ENABLED`); partner writes and external email off by default (§5.5); Order Bridge invisible in the Portal until a permission is granted; other services never call orders-api, so a failing orders-api cannot affect them (the only shared write is task rows in `tasks`, done in a savepoint).

### 14.6 Rollback

Stop `orders-api` and `order-bridge-web` and leave the permissions ungranted. No other service needs a change; the added configuration lines are inert without the services.

### 14.7 Extraction later

After go-live a short review decides, piece by piece, whether to move `orders-api/app/platform/` or `order-bridge/src/platform-ui/` into shared packages (as was done with `packages/authz`). Thanks to the folder rules of §13 this is a move plus an import change. Until then no other module imports them.
