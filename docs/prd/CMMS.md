# CMMS (Maintenance Management) — Product Requirements Document

**Version**: 2.1
**Date**: 2026-10-16
**Status**: Proposed (not built; no code, branch or schedule exists)
**Scope**: Equipment asset management for the Kingston dairy plant — equipment hierarchy, spare parts, preventive maintenance, work orders, maintenance compliance (CFIA/HACCP/TSSA) and administration.
**Source**: Converted from `/srv/uniops/EMS/cmms_prd_en_v2.md` (v2.0, last updated 2026-03-13), cross-checked line by line against the Chinese original `/srv/uniops/EMS/cmms_prd_cn_v2.md` (v2.0, same date). Earlier drafts, design notes and UX mock-ups sit alongside in `/srv/uniops/EMS/` and were not merged into this document.

**What changed in this version (2.0 → 2.1)**
- Put into the common UniOps PRD header format. Requirement IDs (F-EM / F-SM / F-PM / F-WO / F-CB / F-ADM) are unchanged.
- Untranslated Chinese fragments in the English source were translated (F-SM-12 "qualifications", F-CB-02 "corrective action", F-WO-09 "cannot be modified").
- A few details that were in the Chinese original but missing from the English one were restored: the preview warns before parent nodes are auto-created (F-EM-23); parts can be assigned to a specific bin (F-SM-02); food-grade and non-food-grade stock are reported separately (F-SM-05); the workflow engine covers both work-order and procurement approvals (F-ADM-05); equipment with an expired certificate is automatically restricted from operation (F-CB-04). Where the English and Chinese versions differ in substance, the difference is called out in a note.
- Added §0, the platform reconciliation notice, and §6, Open Items. The functional design itself was **not** rewritten.

---

## 0. Read First — Written Before the UniOps Platform Conventions

This PRD was written in March 2026 as a standalone product specification ("Dairy EAM / cMMS"). That was before the UniOps platform conventions were set. Several requirements describe capabilities as if this system had to provide them itself. In UniOps those capabilities already exist as shared platform services, and this module would **consume** them rather than rebuild them:

| This PRD specifies | UniOps platform equivalent | See |
|---|---|---|
| F-ADM-01/02/10 RBAC, button-level permissions, password policy, 2FA, sessions | identity-api + shared `authz` package (permission keys, data scope), Portal login and forced password rotation | PORTAL.md |
| F-ADM-05 visual workflow engine; F-WO-03, F-SM-09, F-SM-11 approval flows | approval-api (the shared approval engine) | PORTAL.md, EPMS.md |
| Alerts, reminders, "pending WOs" lists (F-SM-03, F-EM-15, F-CB-02/04/08, F-WO-05) | Task Inbox (shared `tasks` table) plus the outbox notifier | PORTAL.md |
| F-ADM-03 organisation tree; plant/line structure (F-EM-01) | mdm-api departments and `locations` (the plant-area tree that Safety already uses) | PORTAL.md, SAFETY.md |
| F-SM-10–F-SM-17 requisition, PO, supplier, receipt, returns | EPMS (PR/PO/GR/vendor master), with NC as the system of record for finance | EPMS.md, FINANCE.md |
| F-SM-01–F-SM-08 inventory and bins | Inventory module (WMS + NC ledgers) | INVENTORY.md |
| F-WO-07–F-WO-09 LOTO; F-CB certificates | Safety permits to work (LOTO) and equipment register | SAFETY.md |
| F-ADM-07/08/09 logs, health monitoring, backup | Platform operations (deployment and monitoring), not module features | — |

How each of these is wired is to be decided when the module is built (Open Item CM-01). Until then the requirements below stand as written, as a statement of **what** the plant needs, not **how** UniOps will provide it.

---

## 1. Introduction

### 1.1 Background
This system covers equipment lifecycle management, food-safety compliance (CFIA/HACCP), preventive maintenance and maintenance efficiency for a Canadian dairy manufacturer. It must support a multi-plant, multi-line equipment hierarchy, and every operation must be traceable, auditable and compliant with local regulations such as TSSA.

### 1.2 Target Users
- Equipment Administrators
- Maintenance Technicians
- Warehouse Clerks
- Procurement Specialists
- Quality & Compliance Auditors
- Production Managers
- System Administrators

---

## 2. Core Functional Modules

### 2.1 Equipment Hierarchy Management

#### 2.1.1 Equipment Tree Structure
| ID | Requirement |
|---|---|
| F-EM-01 | Supports a 5-level hierarchy: **Plant → Production Line → Equipment Unit → Component → Part**. |
| F-EM-02 | Users can create, edit (except the unique ID), and deactivate/activate child nodes at any level. |
| F-EM-03 | Each equipment node is assigned a globally unique Equipment ID. |

#### 2.1.2 Visualization & Navigation
| ID | Requirement |
|---|---|
| F-EM-04 | Left-side collapsible/expandable tree view of the equipment structure. |
| F-EM-05 | Real-time search/filter by name, ID or location. |
| F-EM-06 | Multiple user-switchable view modes: classic tree, location grouping, status colouring (Running / Maintenance / Fault), type grouping. |

#### 2.1.3 Equipment Detail Panel (Tabbed Interface)
| ID | Requirement |
|---|---|
| F-EM-07 | Five tabs: **Basic Info, History, Documents, Spare Parts (BOM), Preventive Maintenance Plans**. |
| F-EM-08 | "Basic Info" distinguishes required fields (ID, name, type, location, manufacturer, serial number) from optional ones (specs, warranty, financial code). |
| F-EM-09 | Auto-calculates and displays key metrics: MTBF, MTTR, Health Score. |
| F-EM-23 | **Bulk equipment import** (added in v2.0) — see below. |

**F-EM-23 — Bulk Equipment Import**
- Bulk import via **Excel (.xlsx) or CSV**.
- Required fields: Plant, Production Line, Equipment Name, Type, Manufacturer, Serial Number, Physical Location.
- A downloadable template with field descriptions and format examples.
- Missing parent nodes (e.g. a new production line) are created automatically, but only after the preview clearly flags them for confirmation.
- **Pre-validation**: missing required fields, duplicate serial numbers/names, invalid hierarchies, data format errors.
- A **preview table** highlights errors with row-level diagnostics; the user can correct or re-upload.
- After import: a success/failure report (including assigned Equipment IDs); failed records can be exported.
- Only the Equipment Administrator role can import; every import is audit-logged.
- Maximum 5,000 rows per file. **Import creates only — it never updates existing equipment.**

#### 2.1.4 Maintenance History
| ID | Requirement |
|---|---|
| F-EM-10 | Timeline view (reverse chronological) of all work orders, preventive and corrective. |
| F-EM-11 | Each entry includes maintenance type, date, technician, WO number, description, parts used, duration, root cause. |
| F-EM-12 | Filter by date range, type or technician; export to report. |

#### 2.1.5 Document Management
| ID | Requirement |
|---|---|
| F-EM-13 | Upload PDF, images, CAD, Word, etc. |
| F-EM-14 | Six document categories (e.g. manuals, repair guides); version control and in-browser preview. |
| F-EM-15 | Automatic alerts before key compliance documents expire (e.g. TSSA certificates). |

#### 2.1.6 Spare Parts (BOM) Association
| ID | Requirement |
|---|---|
| F-EM-16 | BOM list shows part ID, name, spec, quantity, safety stock, storage location. |
| F-EM-17 | Many-to-many equipment–part mapping; BOM create/read/update/delete plus bulk import/export. |
| F-EM-18 | The equipment's BOM is shown automatically in its work orders for reference. |

#### 2.1.7 Preventive Maintenance Plan Linkage
| ID | Requirement |
|---|---|
| F-EM-19 | Lists all PM plans linked to the equipment; plans can be created, edited and deleted from here. |

#### 2.1.8 Equipment Identification & QR Codes
| ID | Requirement |
|---|---|
| F-EM-20 | A unique QR code per equipment item (embeds the ID and the system URL). |
| F-EM-21 | Batch printing of QR labels. |
| F-EM-22 | Scanning the QR code on a phone opens the equipment detail page directly. |

---

### 2.2 Spare Parts Management

#### 2.2.1 Master Data & Inventory
| ID | Requirement |
|---|---|
| F-SM-01 | Full part master: SKU, name, spec, stock level, bin location, category, supplier. |
| F-SM-02 | Multiple warehouses/zones; a part can be assigned to a specific bin. |
| F-SM-03 | Automatic alert when stock falls below safety level. |

#### 2.2.2 Food-Grade Parts Management
| ID | Requirement |
|---|---|
| F-SM-04 | Mandatory "Food-Grade" flag; material, certification standard and expiry date are required when set. |
| F-SM-05 | Stock is tracked and reported separately for food-grade and non-food-grade parts; on issue, valid certified food-grade parts are recommended first. |

#### 2.2.3 Parts Usage Tracking
| ID | Requirement |
|---|---|
| F-SM-06 | Every part movement is logged: linked WO, user, date, quantity. |
| F-SM-07 | Usage links to equipment history and supports consumption analytics. |
| F-SM-09 | **Outbound inventory, including general issuance** (updated in v2.0) — see below. |

**F-SM-09 — Outbound Inventory (Including General Issuance)**
- Two outbound types:
  - **Work-order-linked issuance**: bound to an active WO; parts chosen from the BOM or manually.
  - **General issuance**: no WO; the user must give requester, department and purpose (at least 10 characters).
- Methods: barcode scan, search by SKU/name, or select from BOM.
- Issuing a food-grade part **always shows a food-safety compliance warning**.
- Every issue creates a unique outbound record, deducts stock in real time, and feeds usage tracking and the compliance dashboard.
- High-value or controlled parts can require an **approval workflow** for general issuance.
- Only the Warehouse Clerk or Authorized Technician roles may issue parts.

#### 2.2.4 Inbound Process
| ID | Requirement |
|---|---|
| F-SM-08 | Three-step receipt: goods inspection → registration → confirmation; captures batch and expiry. |

#### 2.2.5 End-to-End Procurement
| ID | Requirement |
|---|---|
| F-SM-10 | Requisitions raised from low-stock alerts or manually; bulk upload supported. |
| F-SM-11 | Multi-level approval by amount; full approval history. |
| F-SM-12 | Supplier management: qualifications, performance rating, tiering, blacklist; food-grade suppliers flagged with their certifications. |
| F-SM-13 | POs support standard orders and framework agreements; clear status tracking; partial deliveries allowed. |
| F-SM-14 | Receipt inspection result: Accepted / Partially accepted / Rejected; can trigger a return. |
| F-SM-15 | Returns link to the original PO and affect inventory and cost. |
| F-SM-16 | Procurement reports: demand, execution, supplier KPIs, cost analysis. |
| F-SM-17 | Service procurement (repairs, inspections, consulting) as a dedicated flow, emphasising the vendor's technical qualifications. |

> Note: F-SM-10 to F-SM-17 overlap almost entirely with EPMS (PR → approval → PO → GR, vendor master, agreements). See CM-03.

---

### 2.3 Preventive Maintenance (PM) Planning

| ID | Requirement |
|---|---|
| F-PM-01 | PM plans define triggers (time-based or runtime hours), equipment, task lists, parts and assignees. |
| F-PM-02 | Supports run-to-failure, time-based, condition-based and opportunity-based strategies. *(Chinese original: "routine, condition-based, opportunity-based and other strategies" — no run-to-failure.)* |
| F-PM-03 | Calendar view (month/week/day); drag-and-drop rescheduling. |
| F-PM-04 | Work orders are generated automatically when a plan falls due. |
| F-PM-05 | Hierarchical task checklist with description, steps, standards and estimated hours. |
| F-PM-06 | Includes dairy-specific tasks (cleaning, safety checks, functional tests); photo evidence supported. |
| F-PM-07 / F-PM-08 | On WO completion the next due date is updated automatically and the WO is archived to equipment history. |

---

### 2.4 Work Order Management

| ID | Requirement |
|---|---|
| F-WO-01 | WOs are created from PM, a manual request, an inspection, or a management assignment. |
| F-WO-02 | Three types: Corrective, Preventive, Upgrade/Retrofit. Four priorities: Urgent / High / Medium / Low. |
| F-WO-03 | Customisable approval workflow (e.g. Create → Approve → Assign → Execute → Accept → Close). |
| F-WO-04 | Multi-technician collaboration; a primary owner is assigned. |
| F-WO-05 | Mobile list of pending WOs; offline viewing and data entry. |
| F-WO-06 | Task checklist for step-by-step completion with check marks. |
| F-WO-07 | **LOTO (mandatory)**: for high-risk energy-isolation WOs the mobile app **must show a LOTO confirmation screen** before work starts. |
| F-WO-08 | **LOTO**: instructions are generated from the equipment profile (energy types, isolation points, steps) and require a digital signature. |
| F-WO-09 | **LOTO**: the LOTO record is permanently attached to the WO and cannot be modified. |
| F-WO-10 | Technicians log parts used by scan, search or BOM selection. |
| F-WO-11 | Standardised root-cause taxonomy (mechanical, electrical, operator error, etc.); multi-select plus free text. |
| F-WO-12 | Acceptance requires actual labour hours, parts consumed, root cause, description and Before/After photos. |
| F-WO-13 | On acceptance the WO status becomes "Closed"; all data is archived and immutable. |

---

### 2.5 Compliance Dashboard

| ID | Requirement |
|---|---|
| F-CB-01 | Central register of audit findings; multi-dimensional filtering. |
| F-CB-02 | Tracks corrective-action progress, owner and due date per finding; overdue items alert automatically. |
| F-CB-03 | Manages all equipment certificates (TSSA, electrical safety, etc.) with validity and status. |
| F-CB-04 | Expiry alerts at 90/60/30 days; equipment with an expired certificate is automatically restricted from operation. |
| F-CB-05 | Certificate renewal tracking. |
| F-CB-06 | KPI summary: Compliance Score, Equipment Compliance Rate, Open Findings. |
| F-CB-07 | Trend charts and equipment-level compliance details. |
| F-CB-08 | One compliance alert centre for certificates, audits, corrective actions and maintenance. |
| F-CB-09 | One-click generation of a CFIA/HACCP-compliant "Audit Package". |

---

### 2.6 System Administration

| ID | Requirement |
|---|---|
| F-ADM-01 | Role-based access control (RBAC) with predefined roles (Admin, Technician, Auditor, etc.). |
| F-ADM-02 | Button-level permissions; data-scope control (e.g. "only my department's equipment"). |
| F-ADM-03 | Organisation tree, linked to user, equipment and WO ownership. |
| F-ADM-04 | Global settings: company information, numbering rules, time zone. |
| F-ADM-05 | Visual workflow engine for configuring approvals, including work-order and procurement approvals. |
| F-ADM-06 | Data dictionary for enumerations and code tables. |
| F-ADM-07 | Operational, business and system logs; searchable and exportable. |
| F-ADM-08 | System health monitoring dashboard with alerts. |
| F-ADM-09 | Automated backup and restore policies. |
| F-ADM-10 | Security: strong password policy, 2FA, session management. |

> Note: under UniOps conventions most of §2.6 is provided by the platform rather than by this module. See §0 and CM-01.

---

## 3. Non-Functional & Integration Requirements

| Category | Requirement |
|---|---|
| **Performance** | Web page load ≤ 3 s; mobile app launch ≤ 2 s; 500+ concurrent users |
| **Availability** | ≥ 99.9% uptime; transactional integrity for critical operations |
| **Security & Compliance** | CFIA/HACCP electronic-record rules (data integrity, traceability, e-signatures); encryption at rest and in transit |
| **Compatibility** | Web: latest Chrome/Firefox/Edge. Mobile: iOS 14+ / Android 10+ |
| **Integration** | Standard APIs for ERP (assets, procurement, cost), SCADA (equipment status, runtime hours) and access control (LOTO integration) |
| **Offline Mobile** | SQLite-based local storage; offline WO execution; automatic sync with conflict resolution when back online |

> Note: the "mobile app" in this PRD is a native app with SQLite. Every UniOps module so far is a responsive web front end. Safety, for example, chose responsive web with draft saving instead of full offline mode. See CM-05.

---

## 4. Appendices (referenced in the source, not reproduced here)

- **Glossary**: MTBF, MTTR, LOTO, BOM, CFIA, HACCP, TSSA, RBAC
- **Equipment Hierarchy Diagram**
- **Spare Parts Outbound Flowchart** (WO-linked vs. general issuance)
- **Bulk Equipment Import Template Sample** (.xlsx)
- **Requirement Traceability Matrix** (original PRD → F-XXX IDs)

---

## 5. Version History

| Version | Date | Change |
|---|---|---|
| 1.0 | — | Original PRD (`/srv/uniops/EMS/cmms_prd_CN.md`, `prd_en.md`). |
| 2.0 | 2026-03-13 | Every module broken down into testable F-XXX requirements. Added F-EM-23 bulk equipment import. Enhanced F-SM-09 with general issuance plus food-safety and audit controls. Added the mandatory LOTO workflow, mobile offline capability and granular RBAC. Aligned with CFIA, HACCP and TSSA. |
| 2.1 | 2026-10-16 | Converted into the UniOps PRD set (this document). No change to functional scope. Added §0 and the Open Items. |

---

## 6. Open Items

| ID | Item | Status |
|---|---|---|
| CM-01 | **Reconcile with UniOps platform conventions at build time.** This PRD predates them. Before design, decide how the module plugs into identity/authz (F-ADM-01/02/10), the approval engine (F-ADM-05, F-WO-03, F-SM-09, F-SM-11), the Task Inbox and notifier (all alerts and pending-work lists), mdm departments/locations (F-ADM-03, F-EM-01) and the shared shell/Portal navigation, per PORTAL.md. The rule is to reuse, not rebuild. | Open |
| CM-02 | Equipment hierarchy vs. existing master data: the 5-level Plant → Line → Unit → Component → Part tree must be reconciled with the mdm `locations` tree (Building → Department → Operational Line → Line 1/2) and with Safety's production-equipment register (SAFETY.md E56). Choose one equipment master. | Open |
| CM-03 | Procurement scope (F-SM-10–F-SM-17) duplicates EPMS. Decide whether CMMS raises EPMS PRs and reads POs/GRs, rather than running its own purchasing, supplier and returns flows. NC remains the finance system of record (FINANCE.md). | Open |
| CM-04 | Spare-parts inventory (F-SM-01–F-SM-08) vs. the Inventory module and the WMS/NC stock ledgers (INVENTORY.md): which system owns spare-parts quantities and bins. | Open |
| CM-05 | Native mobile app with SQLite offline sync vs. the UniOps responsive-web pattern. Confirm the offline requirement, or accept draft saving as Safety did. | Open |
| CM-06 | LOTO (F-WO-07–F-WO-09) and equipment certificates (F-CB-03/04) overlap with Safety's LOTO permits to work and equipment register (SAFETY.md E41/E42/E46). Define ownership and the hand-off. | Open |
| CM-07 | SCADA (runtime hours, status) and access-control (LOTO) integrations: no interface or owner identified. | Open |
| CM-08 | F-CB-04 "restrict operation of equipment with an expired certificate": define what restriction means in practice (a block on WOs, a flag only, or an integration with control systems). | Open |
| CM-09 | Minor wording differences between the English and Chinese v2.0 (product name "cMMs" vs. "Dairy EAM"; F-PM-02 strategy list). Confirm which is authoritative. | Open |
| CM-10 | Non-functional targets (500+ concurrent users, 99.9% uptime) are far above single-plant scale and the current UniOps deployment. Re-baseline them. | Open |
| CM-11 | No sponsor, phasing, estimate or schedule recorded. | Open |
