"""Everything in Safety with one person's name on it.

Round 10 (open box): "in the settings register, create a staff register where
the hse and supervisor roles can enter a staffs name, and see every form,
incident, near miss, hazard assessment, document, position, jhsc meeting, or
anything else that has the workers name on it. Sort it into a dashboard with
register groups with a total count of items in that group. provide an excel
based list view under this where the headings are Employee name, date,
register type, and register id number … make each line item a link to the
item for viewing."

★ What a viewer sees is decided here, category by category (review,
uniops-8b):
- Who: HSE anybody; a supervisor their direct reports (users.supervisor_id);
  anyone themselves. Anybody else — and a person who does not exist — is 403.
- Which categories: each one only for a viewer who holds the read key of that
  register. A category the viewer may not open is left out altogether, not
  shown as 0: "Injuries: 0" is itself a statement about somebody's health.
- On their own file, a person also sees what they can already open as theirs
  (their training, their forms, their actions …) — the `own` query.
- Never: an anonymous incident (not even for HSE — who reported what is for
  the incident page, not a summary of a person), a draft, or the medical
  section. Being the injured person is its own category, behind the medical
  key, because summed up per person it is a health record.
- A figure and its list are the same query.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    # The register's own read key; without it the category is not shown.
    permission: str
    # SELECT id, at, ref, title, link_id … for someone else's file. :uid is the person.
    sql: str
    # Where a row opens: formatted with id / ref / link_id. None: nowhere.
    path: str | None
    # On a person's own file without the key: only what they can already open
    # as theirs. None: nothing without the key.
    own: str | None = None
    own_path: str | None = None


_NOT_ANON = "i.status <> 'draft' AND NOT i.is_anonymous"


def _reports(which: str, kind: str) -> str:
    return f"""SELECT i.id, i.occurred_at AS at, i.incident_no AS ref, i.title,
                      i.id AS link_id, '{kind}' AS kind
                 FROM ehs_incidents i
                WHERE {_NOT_ANON} AND {which}
                  AND (i.reported_by = :uid OR EXISTS (
                       SELECT 1 FROM ehs_incident_persons p
                        WHERE p.incident_id = i.id AND p.user_id = :uid AND p.role <> 'injured'))"""


def _own_reports(which: str, kind: str) -> str:
    return f"""SELECT i.id, i.occurred_at AS at, i.incident_no AS ref, i.title, i.id AS link_id,
                      '{kind}' AS kind
                 FROM ehs_incidents i WHERE {_NOT_ANON} AND {which} AND i.reported_by = :uid"""

CATEGORIES: tuple[Category, ...] = (
    Category(
        "incident", "Incidents", "ehs.incident.read",
        _reports("i.form_kind <> 'near_miss'", "Incident"),
        "/incidents/{link_id}",
        own=_own_reports("i.form_kind <> 'near_miss'", "Incident"),
        own_path="/incidents/{link_id}"),
    # ★ Round 11, TC-15: the box counted both under one name.
    Category(
        "near_miss", "Near misses", "ehs.incident.read",
        _reports("i.form_kind = 'near_miss'", "Near miss"),
        "/incidents/{link_id}",
        own=_own_reports("i.form_kind = 'near_miss'", "Near miss"),
        own_path="/incidents/{link_id}"),
    Category(
        "injury", "Injuries", "ehs.incident.medical.read",
        f"""SELECT i.id, i.occurred_at AS at, i.incident_no AS ref, i.title, i.id AS link_id,
                   'Injury' AS kind
              FROM ehs_incidents i
             WHERE {_NOT_ANON} AND EXISTS (
                   SELECT 1 FROM ehs_incident_persons p
                    WHERE p.incident_id = i.id AND p.user_id = :uid AND p.role = 'injured')""",
        "/incidents/{link_id}",
        # Their own injury, where they reported it (and so can open it).
        own=f"""SELECT i.id, i.occurred_at AS at, i.incident_no AS ref, i.title, i.id AS link_id,
                       'Injury' AS kind
                  FROM ehs_incidents i
                 WHERE {_NOT_ANON} AND i.reported_by = :uid AND EXISTS (
                       SELECT 1 FROM ehs_incident_persons p
                        WHERE p.incident_id = i.id AND p.user_id = :uid AND p.role = 'injured')""",
        own_path="/incidents/{link_id}"),
    Category(
        "form", "Forms", "ehs.incident.report",
        """SELECT s.id, s.submitted_at AS at, s.submission_no AS ref,
                  (SELECT name FROM ehs_form_templates t WHERE t.id = s.template_id) AS title,
                  s.id AS link_id, 'Form' AS kind
             FROM ehs_form_submissions s
            WHERE s.status = 'submitted' AND s.submitted_by = :uid""",
        "/forms/submissions/{link_id}"),
    Category(
        "hazard", "Hazard assessments", "ehs.action.read",
        """SELECT h.id, h.identified_on AS at, h.hazard_no AS ref, h.title, h.id AS link_id,
                  'Hazard assessment' AS kind
             FROM ehs_hazards h
            WHERE h.status <> 'draft' AND (h.created_by = :uid OR h.owner_id = :uid)""",
        "/hazards?q={ref}"),
    Category(
        "jsa", "Job analyses signed", "ehs.incident.report",
        """SELECT o.id, o.signed_at AS at, j.jsa_no AS ref, j.title, j.id AS link_id,
                  'Job analysis' AS kind
             FROM ehs_jsa_signoffs o JOIN ehs_jsa j ON j.id = o.jsa_id
            WHERE o.user_id = :uid""",
        "/jsa/{link_id}"),
    Category(
        "document", "Documents acknowledged", "ehs.document.read",
        """SELECT a.id, a.acknowledged_at AS at, d.doc_no AS ref,
                  d.title || ' (version ' || a.version || ')' AS title, d.id AS link_id,
                  'Document' AS kind
             FROM ehs_document_acks a JOIN ehs_documents d ON d.id = a.document_id
            WHERE a.user_id = :uid""",
        "/documents/{link_id}",
        own="""SELECT a.id, a.acknowledged_at AS at, d.doc_no AS ref,
                      d.title || ' (version ' || a.version || ')' AS title, d.id AS link_id,
                      'Document' AS kind
                 FROM ehs_document_acks a JOIN ehs_documents d ON d.id = a.document_id
                WHERE a.user_id = :uid""",
        own_path="/documents/{link_id}"),
    Category(
        "position", "Positions", "ehs.worker.read",
        """SELECT w.id, w.effective_from AS at, p.code AS ref,
                  p.name || CASE WHEN w.effective_to IS NOT NULL
                                 THEN ' (until ' || to_char(w.effective_to, 'YYYY-MM-DD') || ')'
                                 ELSE '' END AS title,
                  p.id AS link_id, 'Position' AS kind
             FROM ehs_worker_positions w JOIN ehs_job_positions p ON p.id = w.position_id
            WHERE w.user_id = :uid""",
        "/settings/positions"),
    Category(
        "jhsc", "JHSC meetings", "ehs.jhsc.read",
        """SELECT a.id, m.held_on AS at, m.meeting_no AS ref,
                  coalesce(m.title, 'JHSC meeting') || ' — ' || a.attendance AS title,
                  m.id AS link_id, 'JHSC meeting' AS kind
             FROM ehs_jhsc_attendance a JOIN ehs_jhsc_meetings m ON m.id = a.meeting_id
            WHERE a.user_id = :uid""",
        "/jhsc/meetings/{link_id}"),
    Category(
        "toolbox", "Toolbox talks signed", "ehs.incident.report",
        """SELECT s.id, s.signed_at AS at, k.talk_no AS ref, k.title, d.id AS link_id,
                  'Toolbox talk' AS kind
             FROM ehs_tbt_signatures s JOIN ehs_tbt_deliveries d ON d.id = s.delivery_id
                  JOIN ehs_toolbox_talks k ON k.id = d.talk_id
            WHERE s.user_id = :uid""",
        "/toolbox-talks?delivery={link_id}"),
    Category(
        "training", "Training", "ehs.training.read",
        """SELECT r.id, r.completed_on AS at, NULL AS ref, r.course_label AS title,
                  r.id AS link_id, 'Training' AS kind
             FROM ehs_training_records r WHERE r.user_id = :uid""",
        None,
        own="""SELECT r.id, r.completed_on AS at, NULL AS ref, r.course_label AS title,
                      r.id AS link_id, 'Training' AS kind
                 FROM ehs_training_records r WHERE r.user_id = :uid""",
        own_path="/training/mine"),
    Category(
        "action", "Corrective actions", "ehs.action.read",
        """SELECT a.id, a.created_at AS at, a.action_no AS ref, a.title, a.id AS link_id,
                  'Corrective action' AS kind
             FROM ehs_actions a WHERE a.owner_id = :uid""",
        "/actions/{link_id}",
        own="""SELECT a.id, a.created_at AS at, a.action_no AS ref, a.title, a.id AS link_id,
                      'Corrective action' AS kind
                 FROM ehs_actions a WHERE a.owner_id = :uid""",
        own_path="/actions/{link_id}"),
    Category(
        "permit", "Work permits", "ehs.permit.read",
        """SELECT p.id, p.created_at AS at, p.permit_no AS ref,
                  p.type_label || ' — ' || p.work_description AS title, p.id AS link_id,
                  'Work permit' AS kind
             FROM ehs_permits p
            WHERE p.status <> 'draft' AND (p.requested_by = :uid OR EXISTS (
                  SELECT 1 FROM ehs_permit_signatures g
                   WHERE g.permit_id = p.id AND g.user_id = :uid))""",
        "/permits/{link_id}",
        own="""SELECT p.id, p.created_at AS at, p.permit_no AS ref,
                      p.type_label || ' — ' || p.work_description AS title, p.id AS link_id,
                      'Work permit' AS kind
                 FROM ehs_permits p WHERE p.status <> 'draft' AND p.requested_by = :uid""",
        own_path="/permits/{link_id}"),
)

ROWS_LIMIT = 2000


def _iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


async def build(db: AsyncSession, person_id: uuid.UUID, *, held: set[str],
                own_file: bool) -> dict:
    """The groups and rows this viewer may see. `held`: which of the
    categories' keys the viewer holds."""
    groups, rows = [], []
    for c in CATEGORIES:
        if c.permission in held:
            sql, path = c.sql, c.path
        elif own_file and c.own:
            sql, path = c.own, c.own_path
        else:
            continue  # not shown at all — not 0
        found = (await db.execute(text(sql), {"uid": person_id})).mappings().all()
        groups.append({"key": c.key, "label": c.label, "count": len(found)})
        for r in found:
            rows.append({
                "category": c.key, "register": r["kind"], "date": _iso(r["at"]),
                "number": r["ref"], "title": r["title"],
                "path": path.format(id=r["id"], ref=r["ref"], link_id=r["link_id"]) if path else None,
            })
    rows.sort(key=lambda r: r["date"] or "", reverse=True)
    return {"groups": groups, "rows": rows[:ROWS_LIMIT], "total": len(rows)}
