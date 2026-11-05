"""Read an ADP roster file and work out what an import would do.

Everything here is pure: bytes in, rows out; rows and the current state in,
groups out. The database is touched in services/staff.py. EMPLOYEES.md §4.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

HEADERS = {
    "position_id": "Position ID",
    "status": "Position Status",
    "payroll_name": "Payroll Name",
    "job_title": "Job Title Description",
    "adp_department": "Home Department Description",
    "work_email": "Work Contact: Work Email",
}
_POSITION_ID = re.compile(r"^ZK7(\d{6})$", re.IGNORECASE)
_STATUS = {"active": "active", "leave": "on_leave"}

# The fields an import owns on an ADP employee (D6). Status is one of them, but
# Left is never set here: people who left are not in the file at all.
ADP_FIELDS = ("payroll_name", "display_name", "work_email", "job_title", "adp_department", "status")


class RosterFileError(ValueError):
    """The whole file is unusable. The message is shown to the user."""


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def display_name(payroll_name: str) -> str:
    """`QIN, PENG FRANK` -> `Peng Frank Qin`."""
    last, sep, first = payroll_name.partition(",")
    name = f"{first.strip()} {last.strip()}" if sep else payroll_name
    return " ".join(name.split()).title()


@dataclass
class RosterRow:
    row: int
    position_id: str
    employee_number: str | None = None
    status: str | None = None
    payroll_name: str = ""
    display_name: str = ""
    job_title: str | None = None
    adp_department: str | None = None
    work_email: str | None = None
    errors: list[str] = field(default_factory=list)


def parse_roster(data: bytes) -> list[RosterRow]:
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, KeyError, OSError) as exc:
        raise RosterFileError("The file is not a readable .xlsx workbook.") from exc
    sheet = workbook[workbook.sheetnames[0]]
    rows = sheet.iter_rows(values_only=True)
    header = [_text(c) for c in next(rows, ())]
    missing = [name for name in HEADERS.values() if name not in header]
    if missing:
        raise RosterFileError(f"Missing column(s): {', '.join(missing)}")
    col = {key: header.index(name) for key, name in HEADERS.items()}

    parsed: list[RosterRow] = []
    for number, values in enumerate(rows, start=2):
        cell = {key: _text(values[i]) if i < len(values) else "" for key, i in col.items()}
        if not any(cell.values()):
            continue
        row = RosterRow(row=number, position_id=cell["position_id"])
        match = _POSITION_ID.match(cell["position_id"])
        if match:
            row.employee_number = match.group(1)
        else:
            row.errors.append(f"Position ID {cell['position_id']!r} is not ZK7 followed by six digits")
        row.status = _STATUS.get(cell["status"].lower())
        if row.status is None:
            row.errors.append(f"Position Status {cell['status']!r} is not Active or Leave")
        row.payroll_name = cell["payroll_name"]
        if not row.payroll_name:
            row.errors.append("Payroll Name is empty")
        row.display_name = display_name(row.payroll_name)
        row.job_title = cell["job_title"] or None
        row.adp_department = cell["adp_department"] or None
        row.work_email = cell["work_email"].lower() or None
        parsed.append(row)
    return parsed


def _changes(row: RosterRow, employee: dict) -> list[dict]:
    return [
        {"field": f, "old": employee.get(f), "new": getattr(row, f)}
        for f in ADP_FIELDS
        if employee.get(f) != getattr(row, f)
    ]


def _brief(row: RosterRow) -> dict:
    return {
        "row": row.row, "employee_number": row.employee_number, "display_name": row.display_name,
        "work_email": row.work_email, "job_title": row.job_title, "adp_department": row.adp_department,
    }


def classify(
    rows: list[RosterRow],
    employees: list[dict],
    user_code_by_email: dict[str, str | None],
    department_map: dict[str, dict],
) -> dict:
    """The groups of IMP-004.

    employees: every employee as a dict of its fields plus `id`.
    user_code_by_email: lower-cased user email -> that user's ERP person code.
    department_map: ADP department ('' = empty) -> {department_id, choose_per_employee}.
    """
    by_number = {e["employee_number"]: e for e in employees}
    number_by_email = {e["work_email"]: e["employee_number"] for e in employees if e.get("work_email")}
    groups: dict[str, list] = {k: [] for k in ("new", "changed", "unchanged", "conflicts", "errors")}

    numbers = [r.employee_number for r in rows if r.employee_number]
    repeated = {n for n in numbers if numbers.count(n) > 1}
    emails = [r.work_email for r in rows if r.work_email and not r.errors]
    repeated_emails = {e for e in emails if emails.count(e) > 1}

    for row in rows:
        if row.employee_number in repeated:
            row.errors.append(f"Employee number {row.employee_number} appears more than once in the file")
        if row.errors:
            groups["errors"].append({"row": row.row, "position_id": row.position_id,
                                     "payroll_name": row.payroll_name, "reasons": row.errors})
            continue

        employee = by_number.get(row.employee_number)
        conflict = None
        if employee and employee["source"] != "adp":
            conflict = "non_adp"
        elif row.work_email in repeated_emails:
            conflict = "email_in_file"
        elif row.work_email:
            owner = number_by_email.get(row.work_email)
            user_code = user_code_by_email.get(row.work_email)
            if owner and owner != row.employee_number:
                conflict = "email_other_employee"
            elif user_code and user_code != row.employee_number:
                conflict = "email_other_user"
        if conflict:
            groups["conflicts"].append({
                **_brief(row), "kind": conflict,
                "employee_id": str(employee["id"]) if employee else None,
                "email_owner": number_by_email.get(row.work_email) or user_code_by_email.get(row.work_email),
            })
        elif employee is None:
            groups["new"].append(_brief(row))
        else:
            changes = _changes(row, employee)
            if changes:
                groups["changed"].append({**_brief(row), "employee_id": str(employee["id"]), "changes": changes})
            else:
                groups["unchanged"].append(_brief(row))

    in_file = {r.employee_number for r in rows if r.employee_number}
    groups["missing"] = [
        {"employee_id": str(e["id"]), "employee_number": e["employee_number"],
         "display_name": e["display_name"], "adp_department": e.get("adp_department")}
        for e in employees
        if e["source"] == "adp" and e["status"] != "left" and e["employee_number"] not in in_file
    ]
    seen = {r.adp_department or "" for r in rows if not r.errors}
    groups["unmapped"] = sorted(d for d in seen if d not in department_map)
    return groups


def counts(groups: dict) -> dict:
    return {k: len(v) for k, v in groups.items()}
