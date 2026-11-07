# UniOps Staff (Employee Master) & ADP Roster Import — Product Requirements Document

**Version:** 0.5
**Date:** 2026-11-04
**Status:** In production; decisions recorded 2026-11-02
**Scope:** A new employee master in UniOps, kept up to date from the ADP Workforce Now roster that HR exports every two weeks; creating UniOps user accounts from employee records; and a to-do list that keeps the NC65 HR person records in step with UniOps, checked automatically by reading NC.

**What changed in v0.5** (2026-11-06)

- A change to an employee's UniOps department or notes takes effect only on **Save** (EMP-004).
- New Org Chart page under MDM: a department chart and reporting lines, generated from existing data and then adjusted by hand (§13).

**What changed in v0.4** (2026-11-06)

- Saving a department mapping now fills in the department for employees waiting on it (MAP-005). It used to reach only later imports, which left people imported first on Needs department.

**What changed in v0.3** (2026-11-05)

- New additional-only role HR holds all four Staff permissions (§8).
- An employee who is not in NC is a notice, not a to-do: people go into NC only as needed (NCT-006). The Add person to-do is removed.

**What this version contains** (v0.2, 2026-11-04)

- Built: the Staff pages, ADP import, department mapping, Create user and bulk create, NC to-dos, the read-back and the reconciliation report. Not built yet: UGEN-005 and UGEN-006 (§6.2).
- Changed while building: reading NC is always a full read (NCR-001), a person whose NC code differs has its own state (NCR-003), to-dos can be closed in bulk (NCT-002), the original file is kept with the import (NFR-002), the NC department code is the UniOps department's own code (MAP-004), and the user link is the ERP person code (UGEN-003).

**Earlier** (v0.1, 2026-11-03)

- First draft. The module is called **Staff** in the UI and lives in Portal's **MDM** group, next to Users and Departments (§2.1). It records the decisions taken on 2026-11-02, the findings from the real ADP roster, the production user list and NC, and the test of NC's import interface for persons (§10).

---

## 1. Overview

### 1.1 Problem

Personnel data in UniOps comes from two places today, and neither is the company's real source:

- **User accounts** are typed in by an administrator or imported from the NC person mirror (PORTAL.md §5.5, USR-005). Each one must carry an NC person code.
- **The NC person mirror** is copied from NC65 (PORTAL.md §5.13). NC is kept by hand, and its person codes had drifted from the HR numbers.

HR's source of truth is **ADP Workforce Now (Canada)**. It is the payroll system, so it is the most accurate list of who works here, in which department and with what job title. When the roster was compared with UniOps and NC on 2026-11-02:

- 78 of the 167 people on the roster had a UniOps account.
- 14 of those 78 accounts had an NC person code that did not match the ADP number: 10 different numbers and 4 with lost leading zeros. One account held another employee's number. These were corrected on 2026-11-02 in UniOps and NC.
- 89 people on the roster (mostly plant staff) have no UniOps account. That is expected, because not every employee needs one.
- 11 active UniOps users are not on the roster. They are head-office staff paid outside ADP, or people who have left.

### 1.2 Decisions (2026-11-02)

| # | Decision |
|---|---|
| D1 | **UniOps is the personnel master.** ADP feeds it. UniOps no longer takes person records from NC. |
| D2 | **Two layers: Employee and User.** An *employee* is a person on the company's books. A *user* is a sign-in account. The ADP import updates employees only. A user is created from an employee record with a button. The employee data fills the form as defaults, and every field can be edited before saving. |
| D3 | **The employee number is the key across ADP, UniOps and NC.** It is the ADP *Position ID* without its `ZK7` prefix: the six digits that UniOps and NC also use (for example, `ZK7000052` → `000052`). |
| D4 | The import uses **manual upload**: HR exports the roster from ADP every two weeks, before payroll, and uploads it. There is no ADP API connection. |
| D5 | **"On Leave" only marks the employee.** The employee stays active, and their user account keeps working. |
| D6 | For an employee who came from ADP, ADP is authoritative for **name, email, job title and ADP department**. Each import overwrites these on the employee. They are **not** copied automatically to the user account (§6.4). |
| D7 | **NC is updated by HR in NC's own HR module, from a to-do list that UniOps generates** (Plan A). UniOps reads NC to confirm that each item was done. Automatic writing into NC is not possible without custom NC development (§10). |

### 1.3 Out of scope

- A direct connection to the ADP API. It needs a paid ADP API subscription and a client certificate, and is a later option.
- Automatic creation or update of NC person records. This is Plan B; see §10.
- Supervisor, hire date and termination date. The current ADP export has none of them (§12, EMP-OI-02).
- Payroll, compensation, banking or any HR data beyond the six roster columns.

---

## 2. Concepts

| Term | Meaning |
|---|---|
| **Employee** | One person employed by the company. Has exactly one employee number. May have no user account. |
| **User** | A UniOps sign-in account (PORTAL.md §4.1). Linked to at most one employee. Service accounts (for example, the NC sync account) are linked to none. |
| **Employee number** | Six digits, unique, the same in ADP, UniOps and NC (D3). |
| **Source** | *ADP*: the employee appears in the ADP roster and is maintained by imports. *Non-ADP*: head-office staff or others paid outside ADP, maintained by hand in UniOps. |
| **ADP department** | The *Home Department Description* in the ADP export, stored exactly as ADP sends it. It is a payroll grouping and does not match UniOps departments one-to-one (§5). |
| **UniOps department** | The department that drives budgets and approval routing (PORTAL.md §5.4). |
| **NC to-do** | One change that HR must make in NC's HR module, generated by UniOps and closed automatically when NC shows the change (§7). |

### 2.1 Where it lives

The module is called **Staff** in the UI. It is an entry in the expandable **MDM** group of the Portal sidebar, next to **Users** and **Departments** (PORTAL.md §3.1, PL-007). Staff has four pages: the **Staff** list and detail (§3), **Import** (§4), **Department Mapping** (§5) and **NC To-do** (§7). The entry is shown to holders of `employee.view` (§8).

---

## 3. Employee record

| Field | Rules |
|---|---|
| Employee number | Required, six digits, unique. Set when the employee is created. Changing it is an administrator correction, recorded in history (EMP-006). |
| Source | ADP or Non-ADP. |
| Payroll name | As ADP sends it, e.g. `QIN, PENG FRANK`. Non-ADP: entered by hand. |
| Display name | Derived from the payroll name as "First Middle Last" in title case, e.g. `Peng Frank Qin`. Used as the default full name when a user is created. |
| Work email | Lower-cased and trimmed. Can be empty. Not used as a key (§4.3). |
| Job title | As ADP sends it. |
| ADP department | As ADP sends it. Can be empty. |
| UniOps department | Set from the department mapping (§5) or chosen by hand. |
| Status | **Active**, **On Leave** or **Left**. *Left* is never set by an import on its own (IMP-009). |
| Left on | Date, entered when an employee is marked Left. |
| Linked user | At most one. |
| NC state | Read from NC: whether NC has the person, NC department and person category, and the NC internal key. Shown, never edited (§7.3). |
| Notes | Free text. |
| History | Every change: field, old value, new value, by whom, when, and whether it came from an import (with a link to it) or a manual edit. |

| FR ID | Requirement |
|---|---|
| **EMP-001** | The **Staff** list (Portal → MDM → Staff) shows employee number, display name, email, job title, ADP department, UniOps department, status, source, linked user and NC state. It follows the standard list rules: sortable headers (EPMS.md §3.20, UI-SORT-001…007), server-side paging and sorting, a locked header row, search by number, name or email, and filters for status, source, UniOps department, has user / no user, and NC state (in step / to-do open / not in NC). |
| **EMP-002** | **Employee detail** shows every field in §3, the history, the linked user (with a link to it), the open NC to-dos, and the differences between employee and user (§6.4). |
| **EMP-003** | **Non-ADP employees** are created and edited by hand. The employee number must be six digits and must not be in use. |
| **EMP-004** | For **ADP employees**, name, email, job title, ADP department and status are read-only in the UI, because the next import overwrites them. UniOps department and notes stay editable. A change to either takes effect only on **Save**; until then the page shows it as not saved. |
| **EMP-005** | **Mark as Left** (both sources) asks for the date. It creates an NC to-do (§7). If the employee has an active user, it offers to deactivate that user through the existing deactivation, which also re-syncs their in-flight approvals (PORTAL.md USR-003). It never deactivates the user silently. **Reinstate** reverses Left. |
| **EMP-006** | **Correct employee number** is allowed for administrators only. It asks for a reason, refuses a number that is already in use, updates the linked user's ERP person code in the same step, records the change in history and creates an NC to-do. |
| **EMP-007** | Employees cannot be deleted; they are marked Left. |
| **EMP-008** | **Export** downloads the filtered list as XLSX. |

---

## 4. ADP roster import

### 4.1 File

The file is the ADP Workforce Now report that HR exports every two weeks before payroll: one sheet with these six columns, found by header name (the column order may change).

| ADP column | Used as | Normalisation |
|---|---|---|
| Position ID | Employee number | Must be `ZK7` followed by six digits; the six digits are the number. Any other format is a row error. |
| Position Status | Status | `Active` → Active, `Leave` → On Leave. Any other value is a row error. |
| Payroll Name | Payroll name | Trimmed; display name derived (§3). |
| Job Title Description | Job title | Trimmed. |
| Home Department Description | ADP department | Trimmed. An empty value is allowed and flagged. |
| Work Contact: Work Email | Work email | Trimmed and lower-cased. ADP sends mixed case, e.g. `NOFTALL@CANADAROYALMILK.COM`. |

The roster lists Active and Leave employees only. **People who have left do not appear in it at all**, so a departure can only be seen as "was on the last roster, missing from this one".

### 4.2 Flow

1. **Upload.** The user picks the file and enters the *roster date* (defaults to today).
2. **Preview.** Nothing is written yet. UniOps shows what the import would do (IMP-004).
3. **Apply.** The user confirms. The changes are written in one step, and the NC to-dos are generated.
4. **Review leavers.** Employees missing from the roster are listed for a decision (IMP-009).

### 4.3 Matching

An import row matches an employee **by employee number only**. Email is used as a cross-check, never as the key: ADP emails can be missing or wrong, and UniOps has role-shared mailboxes.

| Case | Result |
|---|---|
| Number known, email the same or empty on either side | Update |
| Number known, email belongs to a different employee or user | **Conflict**: not applied, shown for a person to resolve |
| Number not known, email not used by any employee | **New** employee |
| Number not known, email already used by another employee | **Conflict** |
| The same number twice in the file | Both rows are errors |

On 2026-11-02 a pure number match would have written one employee's details onto another's account, because that account held the wrong number. The conflict rule exists to stop that case.

### 4.4 Requirements

| FR ID | Requirement |
|---|---|
| **IMP-001** | The import needs the new permission `employee.import` (§8). |
| **IMP-002** | Only `.xlsx` is accepted. The six headers must all be present; otherwise the file is refused with the missing header names. Extra columns are ignored. |
| **IMP-003** | Rows are normalised as in §4.1. A bad row is reported with its row number and reason, and does not stop the other rows. |
| **IMP-004** | **Preview** groups the rows into: **New**; **Changed**, with old → new for each changed field; **Unchanged**; **Conflicts**; **Row errors**; **Unmapped ADP departments** (§5); and **Missing from roster**: ADP employees that are not Left and are not in the file. Each group shows its count and can be expanded. |
| **IMP-005** | **Apply** writes New and Changed rows in one transaction. It is all or nothing. Conflicts and row errors are skipped and listed in the result. |
| **IMP-006** | For an employee whose source is ADP, apply overwrites payroll name, display name, email, job title, ADP department and status (D6). The UniOps department follows MAP-003. |
| **IMP-007** | Apply never changes a user account (§6.4), so an import cannot trigger an approval re-sync. |
| **IMP-008** | A Non-ADP employee whose number appears in the roster is shown as a conflict, with the choice to convert them to ADP. |
| **IMP-009** | **Missing from roster** never marks anyone Left automatically. The list stays on the import result until each employee is either marked Left (EMP-005), or kept with a reason (for example, "on long-term leave, removed from the report by mistake"). A Non-ADP employee is never in this list. |
| **IMP-010** | Importing the same file again changes nothing and creates no new to-dos. |
| **IMP-011** | If the roster date is earlier than the last applied import, the preview warns before apply. |
| **IMP-012** | Only one import can be in preview-to-apply at a time. A second upload is refused until the first is applied or discarded. A preview older than 24 hours must be regenerated before apply. |
| **IMP-013** | **Import history** lists every applied import: roster date, uploaded by, applied at, file name, and the counts per group. It keeps the original file, which can be downloaded by `employee.import` holders, and the per-row results. |
| **IMP-014** | After apply, the result page shows the NC to-dos that the import created (§7) and links to them. |

---

## 5. Department mapping

ADP departments are payroll groups. They are coarser than UniOps departments and named differently. For example, ADP splits Production into Production Shift A, B, C and D plus Production Office, while one ADP department, Marketing and Sales, covers three UniOps departments.

| FR ID | Requirement |
|---|---|
| **MAP-001** | An admin page maps each **ADP department name** either to one UniOps department, or to **Choose per employee**. It needs `employee.edit`. |
| **MAP-002** | An ADP department name seen for the first time is **unmapped**. The preview lists it. Apply still updates the employee's ADP department, but leaves their UniOps department unchanged and flags them as needing a department. |
| **MAP-003** | The UniOps department is a **default, not a rule**. The import sets it from the mapping only when the employee is new or their ADP department changed. A department chosen by hand otherwise survives later imports. For *Choose per employee*, the import never sets it. |
| **MAP-004** | A UniOps department's **code is its NC department code** (0100 to 0113). NC to-dos use it to tell HR which NC department to choose (§7). A department that NC does not have, such as Business Development, cannot show as different from NC. |
| **MAP-005** | Saving a mapping to a UniOps department also gives that department to every employee of that ADP department who is **waiting on Needs department**, and the page says how many. An employee with a department chosen by hand is not waiting and is not changed. Employees under *Choose per employee* keep waiting until someone picks their department. The new departments are compared with NC straight away, against the last NC read. |

Initial mapping, from the 2026-11-02 roster (HR to confirm):

| ADP department | UniOps department | Employees on roster |
|---|---|---|
| Production Shift A / B / C / D | Production | 14 / 8 / 8 / 19 |
| Production Office | Production | 9 |
| Engineering | Engineering | 36 |
| Quality Assurance | Quality Assurance (R&D staff are set per employee) | 21 |
| Finance | Finance | 7 |
| Administration & HR | Human Resources | 8 |
| Logistics & Supply Chain | Supply Chain | 14 |
| Purchasing | Supply Chain | 2 |
| Office Of General Manager | General Manager Office | 5 |
| Marketing and Sales | *Choose per employee* (Marketing / Sales / E-COM) | 15 |
| (empty) | *Choose per employee* | 1 |

UniOps department → NC department, as NC has them today:

| UniOps | NC | | UniOps | NC |
|---|---|---|---|---|
| General Manager Office | 0100 General Manager | | Supply Chain | 0107 Supply Chain |
| Human Resources | 0101 Human Resource | | Project | 0108 Project |
| Finance | 0103 Finance | | R&D | 0109 R&D |
| Production | 0104 Production | | Sales | 0110 SALES |
| Quality Assurance | 0105 Quality Assurance | | Marketing | 0111 Marketing |
| Engineering | 0106 Engineering | | E-COM | 0113 E-com |
| Business Development | *to confirm* (no NC department) | | | |

NC's 0102 Purchasing is marked "NOT USE" in NC and is not mapped.

---

## 6. Users from employees

### 6.1 Creating a user

| FR ID | Requirement |
|---|---|
| **UGEN-001** | The Staff list and employee detail have **Create user** for an employee who has no linked user and is not Left. It needs the existing permission to manage users. |
| **UGEN-002** | The form is the normal user form (PORTAL.md USR-002), filled with defaults: full name = display name, email = work email, department = UniOps department, ERP person code = employee number, primary role = Requester, Supervisor empty. Every field can be changed before saving (D2), except the ERP person code. It is the link to the employee and is changed only through EMP-006. |
| **UGEN-003** | On save, the user is linked to the employee: the link is the user's ERP person code being equal to the employee number. One employee has at most one user. |
| **UGEN-004** | **Bulk create** from the list: tick employees and create users with the defaults above, all as Requesters. Rows that fail (for example, no email, or an email already in use) are reported, and the others are kept. Temporary passwords are shown once, as in USR-005 today. |

### 6.2 Adding a user without an employee

*UGEN-005 and UGEN-006 are not built yet. They should go live after the first import and after every active user has been classified, because until then a new user could not meet the rule.*

| FR ID | Requirement |
|---|---|
| **UGEN-005** | MDM → Users keeps **Add user**, but a new user must either pick an employee or be marked **Service account**. A service account has no employee and no ERP person code. Today a user cannot be created without an ERP person code; that requirement is replaced by this rule. |
| **UGEN-006** | **From ERP** (USR-005) is retired, because NC is no longer the source of persons (D1). |

### 6.3 Linking existing users (one-time)

| FR ID | Requirement |
|---|---|
| **UGEN-007** | At go-live, every user whose ERP person code equals an employee number is linked to that employee. As of 2026-11-02, all 78 users on the roster match. Active users with no employee are listed for an administrator, who either creates a Non-ADP employee and links it, or marks the user as a service account. |

### 6.4 When employee and user differ

The employee and the user are separate records (D2). An import changes the employee, never the user. This is deliberate:

- UniOps names are the names people use (`Frank Qin`), while ADP has payroll names (`QIN, PENG FRANK`). 50 of the 78 linked people differ in this way.
- A user's department drives budgets and approval routing, and ADP departments are coarser (§5).
- A user's email is their sign-in.

| FR ID | Requirement |
|---|---|
| **UGEN-008** | Employee detail and user edit show **differences** between the two records for name, email, department and active status, e.g. "Employee is Left but user is active". |
| **UGEN-009** | Each difference has **Apply to user**. A department change goes through the normal user update, so it re-syncs that user's in-flight approvals. An email change warns that the person's sign-in changes. |
| **UGEN-010** | The Staff list can be filtered to "user differs from employee", so an administrator can work through the differences after each import. |

---

## 7. NC to-do list (Plan A)

NC65 keeps people in its **HR module**. HR is enabled for the Canadian company, and every person there was created through HR. UniOps cannot write there (§10), so HR makes the change in NC, and UniOps checks that it was made.

### 7.1 What creates a to-do

| Trigger | To-do | What HR does in NC |
|---|---|---|
| UniOps department changed | **Change department** | Transfer the person to the NC department shown. |
| Marked Left | **Terminate** | Process the departure in NC HR (person category *Left*, 0201). |
| Reinstated | **Rehire** | Reverse the departure in NC HR. |
| Employee number corrected (EMP-006) | **Change code** | Change the NC person code from the old number to the new one. |
| Read-back finds a difference with no open to-do (§7.3); Not in NC is not one (NCT-006) | **Fix mismatch** | As described on the item. |

Name and email changes do not create to-dos. NC uses the person for document makers and claim payees, and the NC name is informational.

### 7.2 Requirements

| FR ID | Requirement |
|---|---|
| **NCT-001** | The **NC To-do** page lists open and closed items: employee number, name, type, target values (NC department code and name, person category, new code), created at, created by (the import or the user), closed at, and how it was closed. It follows the standard list rules (EMP-001). It can be exported to XLSX so that it can be worked from a printout. |
| **NCT-002** | A to-do **closes automatically** when the read-back (§7.3) shows NC in the target state. HR can also close one or many by hand with a reason, for example "not needed: temporary contractor". A to-do closed by hand is not raised again by the read-back for the same difference. |
| **NCT-003** | A new to-do for an employee supersedes an open one of the same type. For example, two department changes in a row leave one open to-do, with the latest department. |
| **NCT-004** | Holders of the new permission `employee.nc_todo` get **one Task Inbox task** while any to-do is open: "NC person updates pending: N". It opens the NC To-do page and closes itself when none are left. It follows the Task Inbox and notification-outbox rules (PORTAL.md §3.3, §7). |
| **NCT-005** | A to-do open for longer than a configurable number of days (default 14, one payroll cycle) is highlighted on the page and in the task. |
| **NCT-006** | Not every employee belongs in NC, and HR adds people there only as needed. An employee who is not in NC raises **no to-do and no task**. It is a notice: the NC To-do page shows how many there are, the reconciliation report lists them, and the Staff list can filter on them. To add someone, create the person in NC HR with code = employee number, the name, NC department (MAP-004) and person category *Regular employee* (0101); the next read-back shows them In step. Nobody is asked to change a person NC has never had, so a department change, Left, reinstatement or number correction on such an employee raises no to-do either. |

### 7.3 Reading NC back

| FR ID | Requirement |
|---|---|
| **NCR-001** | UniOps reads NC persons after the existing ERP master-data sync (PORTAL.md §5.13), after every import, and on **Check NC now**. This is a **read-only check**. It never creates or changes users, and on employees it changes only the NC state. It always reads the **complete** list: NC lists enabled people only, so a person terminated in NC is seen only by being absent. The person mirror cannot show that, and it keeps old rows after a code change. |
| **NCR-002** | NC persons are matched to employees by **NC internal key** once known, and by person code before that. A person whose code changes in NC therefore stays the same person and does not leave an outdated duplicate. A person who disappears from NC is marked *not in NC*. |
| **NCR-003** | For each employee, the NC state is one of: **In step**; **Not in NC**; **Different department**; **Different status** (Left in UniOps, active in NC); **Different code** (NC has the person under another code); or **Not in UniOps** (an NC person with no employee). |
| **NCR-004** | A **reconciliation report** lists every difference across the whole population, not only open to-dos. On 2026-11-02, NC had 74 active persons who are not on the ADP roster. Many are former staff never terminated in NC, and some have no name. The first report becomes the clean-up list for HR. |

---

## 8. Permissions

Employee data is personal information. It is **not** readable by every signed-in user.

| Permission key | Allows | Default holders |
|---|---|---|
| `employee.view` | Staff list and detail, NC To-do page, reconciliation report | System Admin, HR |
| `employee.edit` | Edit employees, Non-ADP create, Mark as Left / Reinstate, department mapping | System Admin, HR |
| `employee.import` | Upload, preview and apply ADP imports; import history and original files | System Admin, HR |
| `employee.nc_todo` | Close to-dos by hand; receives the Task Inbox task | System Admin, HR |

HR staff get these through the **HR** role, which is additional-only: they keep their own primary role and are given HR in Access Control (PORTAL.md §4.2). Any number of people can hold it. Correcting an employee number (EMP-006) is for System Admin only. User creation and Apply to user keep their existing permission, so HR cannot do them.

The AI Assistant's guide layer must describe the Staff module, the import and the NC to-dos when they are built. Employee data is only available to the Assistant for users who hold `employee.view`.

---

## 9. One-time setup

1. **Clean the data.** Done 2026-11-02: user ERP person codes and NC person codes now match ADP for every user on the roster. One NC person still has an old code and must be changed in NC (EMP-OI-04).
2. **Classify the 11 active users not on the roster.** HR marks each as head-office or Non-ADP (create a Non-ADP employee) or left (deactivate). Pending (EMP-OI-01).
3. **First import.** Upload the current roster. All 167 rows are New. Apply. UGEN-007 links the 78 existing users.
4. **Department mapping.** Confirm §5. Set the UniOps department by hand for *Choose per employee* rows. Set the NC department code on each UniOps department.
5. **First reconciliation.** Run the read-back, review the report (NCR-004) with HR, and create to-dos or close items with reasons.
6. **Lock NC.** Once UniOps is live, person changes in NC HR are made only from NC to-dos. Otherwise the two systems drift apart again.

---

## 10. Why NC cannot be written automatically (verification, 2026-11-02)

NC65's external exchange interface (XChange) has a person-record import. It was tested on the test NC, a copy of production, with throw-away employee numbers:

- **It is not usable as installed.** A stale registration from an older NC version stops it from loading. The same stale entry is present on production. On the test NC it was fixed by a configuration change, which took effect without a restart.
- **The HR module then rejects it.** With HR enabled, NC sends every person save through an HR check. That check fails with an internal error when called from the exchange interface, and this cannot be worked around from the import file.
- **It would be the wrong path anyway.** The import writes the basic person record, not the HR records (entries, job history), so NC HR and NC's person list would disagree. The import also recognises only people it created itself. People entered by hand in NC, which is everyone today, cannot be updated through it without first seeding its cross-reference table.

**Plan B (future):** custom NC development by Yonyou or an NC developer: an import that calls the HR module's own entry, transfer and termination services. UniOps would then send the to-dos in §7.1 to NC instead of listing them for HR. The to-do model in §7 is designed so that Plan B replaces only the "HR does it" step.

---

## 11. Non-functional requirements

| ID | Requirement |
|---|---|
| **NFR-001** | Preview of a 500-row roster returns within 10 seconds; apply within 30 seconds. |
| **NFR-002** | The uploaded roster file is stored with its import record and can be read only by `employee.import` holders. It should be kept for 24 months, then deleted, and the import result rows stay. The deletion is not built yet. |
| **NFR-003** | Every change to an employee is in its history (§3). Imports, Left/Reinstate, number corrections and manual to-do closes are also audit events with user and time. |
| **NFR-004** | Lists and forms follow UniOps UI conventions: English-only UI, plant-local dates, standard sortable lists with locked headers, and no date-only values shifted by time zone. |

---

## 12. Open items

| ID | Item | Status |
|---|---|---|
| EMP-OI-01 | HR to classify the 11 active UniOps users not on the roster (head-office / Non-ADP, or left) | Open (HR) |
| EMP-OI-02 | Ask HR whether the ADP report can add **Reports To** (supervisor), **Hire Date** and **Termination Date**. With Reports To, the Supervisor default for new users could come from ADP; with Termination Date, departures could be shown instead of inferred. | Open (HR) |
| EMP-OI-03 | Confirm the department mapping in §5, including which Quality Assurance staff belong to R&D, and the NC department for Business Development | Open (HR / Finance) |
| EMP-OI-04 | One NC person still carries an old code (ADP 000995 is 100100 in NC); two employees on the roster (000750, 000775) are not in NC yet | Open (NC) |
| EMP-OI-05 | Who in HR receives `employee.import` and `employee.nc_todo` | Open |
| EMP-OI-06 | Whether name changes should also create NC to-dos (§7.1 currently says no) | Open |
| EMP-OI-07 | Plan B: estimate for custom NC import into the HR module (§10) | Future |
| EMP-OI-08 | ADP API connection or automatic pick-up of an emailed roster, instead of manual upload (D4) | Future |
| EMP-OI-09 | Ownership of the users table moving from EPMS to MDM. This is separate work; this PRD does not depend on it. | Future |

---

## 13. Org chart

Portal → MDM → **Org Chart** shows how the company is really organised. It has two views: **Departments** and **Reporting Lines**.

**It is separate from approvals.** Many real supervisors have no Supervisor role in UniOps, on purpose, to keep purchasing approvals short. The chart shows them as they are. It never reads or writes a user's role or Supervisor field, and changing it changes no approver.

| FR ID | Requirement |
|---|---|
| **ORG-001** | **Generate first version** works out the chart from existing data. Holders of `employee.edit` can run it again at any time as **Regenerate**. It fills in what is missing and refreshes generated lines. Anything set by a person stays. |
| **ORG-002** | **Department boxes.** The first version has the company at the top with the General Manager, an Operations box with the Operations Manager, and every active department under whichever of the two Approval Routing names for it. A department's head is its routing Director, else its highest-level employee by job title. An approval role does not make someone the head; a Department Manager counts only as a candidate, and wins a tie on level. A department head and a reporting line are set separately: changing one does not change the other, and Regenerate leaves existing heads alone. |
| **ORG-003** | A box can be moved, given another head or no head, and given a style (plain, green, purple or dashed for people outside the team). A department's name comes from MDM → Departments, and a department box cannot be removed. |
| **ORG-004** | **Reporting lines, first version.** The level comes from the job title: General Manager and Chief; Director and Operations Manager; Manager; Supervisor; Team Lead and Lead Hand; everyone else. Key Accounts and Project Managers count as staff. A person reports to the nearest higher level in the same ADP department, so a shift's staff report to that shift's lead, and someone there in the same trade comes first, so an electrician goes to the electrical lead. Failing that, to the nearest Supervisor or above in the same UniOps department; then the department's head; then the General Manager or Operations Manager under Approval Routing. |
| **ORG-005** | When several people are equally near, the first by employee number is used and the line is marked **To check**. A person with no department is marked too. |
| **ORG-006** | A line can be changed by dragging a card onto the new manager or in the panel. **Confirm this line** keeps a generated line as it is. Either way it counts as set by a person: it is no longer marked To check and Regenerate leaves it. The change is recorded in the employee's history. Nothing is refused: a card can go under someone below it, and that person then comes up to the card's old place, so A → B → C with A moved under C becomes C → A → B. Dropped beside its own report, a card and that report become peers. |
| **ORG-007** | Left employees are not on the chart. Their reports are shown at the top of the chart until they are given a new manager. New employees from an import have no line until Regenerate is run or someone sets one. |
| **ORG-008** | Viewing needs `employee.view`. Changing anything needs `employee.edit`. |
| **ORG-009** | In both views a box can be **added** from any card, in three positions: **Below** (a report), **Beside** (same level) and **On the line** (beside the line under the card, like an assistant: under that card and above the boxes below it). A card already placed can be switched between Below and On the line. |
| **ORG-010** | An added box on Reporting Lines is free text: a name and title for someone with no employee record, such as a head-office leader. Employees can report to it. It can be renamed and removed; removing it moves what was under it up a level. An employee's box cannot be removed and its name comes from Staff, but it can show a **title on the chart** that differs from the job title; the employee record does not change, and clearing it brings the job title back. |
| **ORG-011** | Dragging a card onto the **middle** of another puts it below that card. Dragging it to the **edge** of another puts it next to that card at the same level, so the order of peers can be changed; a line shows where it will land. Changing only the order of peers is not a new reporting line. The order is kept across Regenerate. |
