# UniOps — Product Requirements Documents

This folder holds the **authoritative, maintained PRD for every UniOps module**. All PRDs were brought up to date on 2026-10-16 and are written in English.

- **Built modules** describe what the system on `main` actually does; every requirement was checked against the code. Requirements that are wanted but not built are marked **Not implemented** and listed in each document's Open Items table, never described as working.
- **Planned modules** are proposals or work in progress; their status line says how far they have got.

## Built modules (on `main`)

| Module | Document | Version | Status | Apps / services |
|---|---|---|---|---|
| Portal & platform | [PORTAL.md](PORTAL.md) | 2.2 | In production | portal, identity-api, approval-api, mdm-api, file-api |
| EPMS — procurement | [EPMS.md](EPMS.md) | 2.29 | In production | epms, epms-api |
| OA — reimbursement & travel | [OA.md](OA.md) | 3.1 | In production | oa, expense-api |
| Finance | [FINANCE.md](FINANCE.md) | 2.4 | In production | finance, finance-api |
| AI Financial Analysis | [AI-FINANCIAL-ANALYSIS.md](AI-FINANCIAL-ANALYSIS.md) | 1.0 | In production (Phases 1–5); Phase 6 not started | finance, finance-api |
| Budget | [BUDGET.md](BUDGET.md) | 2.0 | In production | budget-api (UI in finance / epms) |
| MRP | [MRP.md](MRP.md) | 2.1 | In production | mrp, mrp-api |
| Inventory | [INVENTORY.md](INVENTORY.md) | 1.1 | In production | inventory, inventory-api |
| VMS — visitor management | [VMS.md](VMS.md) | 2.10 | In production | vms, vms-api |
| Booking — meeting rooms | [BOOKING.md](BOOKING.md) | 1.2 | In production | booking, booking-api |

## Planned / in development

| Module | Document | Version | Status |
|---|---|---|---|
| Safety (EHS) | [SAFETY.md](SAFETY.md) | 0.5 | In development: built on branch `feature/ehs-safety`, not on `main`, not deployed |
| VMS visitor self-registration & QR check-in | [VMS-SELF-REGISTRATION.md](VMS-SELF-REGISTRATION.md) | 0.2 | Proposed: stakeholder decisions pending |
| Order Bridge (migration from Zoho) | [ORDER-BRIDGE.md](ORDER-BRIDGE.md) | 1.0 | Proposed: in team review |
| CMMS — maintenance management | [CMMS.md](CMMS.md) | 2.1 | Proposed: not started |

## Where shared behaviour is specified

Some behaviour is shared by every module. Each topic is specified once, and the other PRDs cross-reference it:

| Topic | Specified in |
|---|---|
| Sign-in, MFA, passwords, sessions, global sign-out | PORTAL.md §2 |
| Roles, Access Control matrix, Approval Routing, Approval Delegation | PORTAL.md §4, §5.9–5.11; EPMS.md §1 |
| Approval engine: chains, auto-skip, return/resubmit | EPMS.md §3.3; PORTAL.md §5.8 |
| Task Inbox and notification outbox (immediate / digest per task type) | EPMS.md §3.7, §3.9; PORTAL.md §5.7 |
| Data Maintenance | PORTAL.md §5.14; EPMS.md §3.6.8 |
| AI Assistant and UI conventions (tabs, embedding, dates) | EPMS.md §3.19, §3.20 |
| Sortable list columns (click a header: ascending, then descending; server-sorted when paged) | EPMS.md §3.20 (UI-SORT-001…007) |

## Maintenance rules

1. **A change to user-visible behaviour or a business rule updates the PRD in the same branch.** Edit the affected section, add a line to the version summary at the top of the document, and bump its version and date.
2. **No aspirational text.** A requirement that is wanted but not built is marked **Not implemented** and listed in Open Items.
3. **A PRD is a business document.** Keep file paths, line numbers and commit SHAs out of the body. Design notes belong in `docs/superpowers/specs/`.
4. **One PRD per module, kept in this folder.** Do not keep copies elsewhere. A new module starts with a new file here and a row in this index.
5. When a planned module reaches `main`, verify its PRD against the code and move its row to the built table.

## History

Before 2026-10-16 the PRDs were spread across the repo and partly in Chinese:

| Former file | Now |
|---|---|
| `epms/docs/PRD.md` | [EPMS.md](EPMS.md) |
| `epms/docs/PRD-PORTAL.md` | [PORTAL.md](PORTAL.md) |
| `epms/docs/PRD-OA.md` and the root `PRD-OA.md` (merged) | [OA.md](OA.md) |
| `VMS_PRD_Visitor_Management_System.md` | [VMS.md](VMS.md) |
| `MeetingRoomAppointPRD.md` (Chinese) | [BOOKING.md](BOOKING.md) |
| `budget-api/docs/PRD.md` (Chinese) | [BUDGET.md](BUDGET.md) |
| `docs/Finance-Module-PRD.md` and `docs/Finance-Module-Full-PRD.md` (Chinese, merged) | [FINANCE.md](FINANCE.md) |
| MRP PRD for a single plant, phase 1 (Chinese, never committed) | [MRP.md](MRP.md) |
| Safety PRD v0.4 and its reply (Word documents) | [SAFETY.md](SAFETY.md) |
| CMMS PRD v2 (outside the repo) | [CMMS.md](CMMS.md) |
| Order Bridge PRD on the review board (2026-10-09) | [ORDER-BRIDGE.md](ORDER-BRIDGE.md) |
| VMS self-registration proposal on the review board (v0.2) | [VMS-SELF-REGISTRATION.md](VMS-SELF-REGISTRATION.md) |

Inventory and AI Financial Analysis had no PRD before; theirs were written from the code.
