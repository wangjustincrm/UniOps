"""Rewrite register-only first aid as incident reports

Round 5, answer box 6: "rewrite them as incident reports and remove the
register."

First aid moved onto the injury form two rounds ago, and the register became a
merged view: the rows entered here before the move, plus every treatment
recorded on an incident since. HSE has called the register redundant in three
consecutive rounds, and the only thing keeping it was the handful of rows that
existed nowhere else. This converts those, so every treatment is an incident and
there is one place a first-aid case lives.

What a converted row becomes:

  · an incident, form_kind `medical`, incident_type `first_aid` — which is what
    these are, and it puts them in the injury figures they were missing from;
  · the injured person as an `injured` row carrying the treatment given, and the
    first aider as a `first_aider` row where one was recorded;
  · `extra.from_first_aid_log` holding the old FA number, so a paper file or an
    email that cites it still leads somewhere.

★ The log row is **deleted**, not left behind. Copying it would put the same
event in two places, which is the exact fault the merge was for.

★ Every row must convert or the migration aborts naming the ones that did not —
the rule 0043 and 0048 already follow. A statutory record half-moved is worse
than one not moved.

★ The downgrade rebuilds the log rows from the incidents it created and removes
those incidents, which is possible because every field came across. It is not
cosmetic: these are five-year retention records.

Revision ID: 20261005_0051
Revises: 20260930_0050
"""
import json
import uuid

from alembic import op
import sqlalchemy as sa

revision = "20261005_0051"
down_revision = "20260930_0050"
branch_labels = None
depends_on = None

MARK = "from_first_aid_log"


def upgrade() -> None:
    bind = op.get_bind()

    rows = bind.execute(sa.text("""
        SELECT id, log_no, occurred_at, created_at, location_id, location_path,
               injured_user_id, injured_name, first_aider_id, first_aider_name,
               body_part_id, body_part_label, treatment_given, sent_offsite,
               follow_up, file_ids
          FROM ehs_first_aid_log
         WHERE incident_id IS NULL
         ORDER BY occurred_at
    """)).mappings().all()
    if not rows:
        return

    # Continue the incident numbering rather than inventing a parallel series:
    # these become incidents and have to look like the others in every list.
    year = max(r["occurred_at"].year for r in rows)
    highest = bind.execute(sa.text("""
        SELECT COALESCE(MAX(SUBSTRING(incident_no FROM 10)::int), 0)
          FROM ehs_incidents
         WHERE incident_no LIKE :pattern
    """), {"pattern": f"INC-{year}-%"}).scalar() or 0

    unconverted: list[str] = []
    for offset, row in enumerate(rows, start=1):
        if not row["treatment_given"] or not row["injured_name"]:
            # Both are NOT NULL in the register, so this cannot happen — and if
            # it somehow does, an incident with no injured person and no
            # treatment is not a record of anything.
            unconverted.append(f"{row['log_no']}: no injured name or no treatment")
            continue
        # ★ The register holds attachments as a bare list of file ids; an
        # incident's attachments carry a filename, a type and a size, and
        # inventing those is not on. A row with files is reported rather than
        # converted without them — a photograph of an injury is part of the
        # record.
        if row["file_ids"]:
            unconverted.append(
                f"{row['log_no']}: has {len(row['file_ids'])} attachment(s), which "
                f"cannot be moved without their filenames — move them by hand first")
            continue

        incident_id = uuid.uuid4()
        number = f"INC-{year}-{highest + offset:04d}"
        title = f"First aid — {row['body_part_label'] or row['injured_name']}"

        bind.execute(sa.text("""
            INSERT INTO ehs_incidents (
                id, incident_no, form_kind, title, status, occurred_at,
                employer_aware_at, location_id, location_path, injury_class,
                mol_reportable, is_anonymous, description,
                submitted_at, extra, created_at, updated_at
            ) VALUES (
                :id, :no, 'medical', :title, 'submitted', :occurred_at,
                :occurred_at, :location_id, :location_path, 'first_aid',
                FALSE, FALSE, :description,
                :submitted_at, CAST(:extra AS jsonb), now(), now()
            )
        """), {
            "id": incident_id,
            "no": number,
            "title": title[:200],
            "occurred_at": row["occurred_at"],
            "location_id": row["location_id"],
            "location_path": row["location_path"],
            "description": row["treatment_given"],
            # The register has no "reported at" of its own, so the row's own
            # creation time is the honest stand-in. `employer_aware_at` is the
            # occurrence for the same reason — and first aid starts no statutory
            # clock, so nothing hangs off the difference.
            "submitted_at": row["created_at"] or row["occurred_at"],
            "extra": json.dumps({MARK: row["log_no"], "incident_type": "first_aid"}),
        })

        bind.execute(sa.text("""
            INSERT INTO ehs_incident_persons (
                id, incident_id, role, user_id, person_name, body_part_id,
                body_part_label, treatment, sent_offsite, follow_up,
                created_at, updated_at
            ) VALUES (
                :id, :incident_id, 'injured', :user_id, :name, :body_part_id,
                :body_part_label, :treatment, :sent_offsite, :follow_up,
                now(), now()
            )
        """), {
            "id": uuid.uuid4(), "incident_id": incident_id,
            "user_id": row["injured_user_id"], "name": row["injured_name"][:255],
            "body_part_id": row["body_part_id"],
            "body_part_label": row["body_part_label"],
            "treatment": row["treatment_given"],
            # ★ These two have columns of their own on the person row. They were
            # nearly written into `extra`, which would have hidden them from
            # every screen that reads an injury.
            "sent_offsite": bool(row["sent_offsite"]),
            "follow_up": row["follow_up"],
        })

        if row["first_aider_name"] or row["first_aider_id"]:
            bind.execute(sa.text("""
                INSERT INTO ehs_incident_persons (
                    id, incident_id, role, user_id, person_name,
                    created_at, updated_at
                ) VALUES (
                    :id, :incident_id, 'first_aider', :user_id, :name, now(), now()
                )
            """), {
                "id": uuid.uuid4(), "incident_id": incident_id,
                "user_id": row["first_aider_id"],
                "name": (row["first_aider_name"] or "Not named")[:255],
            })

        bind.execute(sa.text("DELETE FROM ehs_first_aid_log WHERE id = :id"),
                     {"id": row["id"]})

    if unconverted:
        raise RuntimeError(
            "These first-aid entries could not be rewritten as incidents, and "
            "guessing at a statutory record is not an option. Fix them by hand, "
            "then re-run:\n  " + "\n  ".join(unconverted))


def downgrade() -> None:
    bind = op.get_bind()

    made = bind.execute(sa.text(f"""
        SELECT i.id, i.incident_no, i.occurred_at, i.location_id,
               i.location_path, i.description, i.extra
          FROM ehs_incidents i
         WHERE i.extra ? '{MARK}'
    """)).mappings().all()

    for row in made:
        extra = row["extra"] or {}
        person = bind.execute(sa.text("""
            SELECT user_id, person_name, body_part_id, body_part_label,
                   treatment, sent_offsite, follow_up
              FROM ehs_incident_persons
             WHERE incident_id = :id AND role = 'injured'
             LIMIT 1
        """), {"id": row["id"]}).mappings().first()
        aider = bind.execute(sa.text("""
            SELECT user_id, person_name FROM ehs_incident_persons
             WHERE incident_id = :id AND role = 'first_aider' LIMIT 1
        """), {"id": row["id"]}).mappings().first()

        bind.execute(sa.text("""
            INSERT INTO ehs_first_aid_log (
                id, log_no, incident_id, occurred_at, location_id,
                location_path, injured_user_id, injured_name, first_aider_id,
                first_aider_name, body_part_id, body_part_label,
                treatment_given, sent_offsite, follow_up, file_ids,
                created_at, updated_at
            ) VALUES (
                :id, :log_no, NULL, :occurred_at, :location_id,
                :location_path, :injured_user_id, :injured_name, :first_aider_id,
                :first_aider_name, :body_part_id, :body_part_label,
                :treatment, :sent_offsite, :follow_up, '[]'::jsonb,
                now(), now()
            )
        """), {
            "id": uuid.uuid4(),
            "log_no": extra.get(MARK),
            "occurred_at": row["occurred_at"],
            "location_id": row["location_id"],
            "location_path": row["location_path"],
            "injured_user_id": person["user_id"] if person else None,
            "injured_name": (person["person_name"] if person else "Not named"),
            "first_aider_id": aider["user_id"] if aider else None,
            "first_aider_name": aider["person_name"] if aider else None,
            "body_part_id": person["body_part_id"] if person else None,
            "body_part_label": person["body_part_label"] if person else None,
            "treatment": (person["treatment"] if person and person["treatment"]
                          else row["description"]),
            "sent_offsite": bool(person["sent_offsite"]) if person else False,
            "follow_up": person["follow_up"] if person else None,
        })
        bind.execute(sa.text("DELETE FROM ehs_incident_persons WHERE incident_id = :id"),
                     {"id": row["id"]})
        bind.execute(sa.text("DELETE FROM ehs_incidents WHERE id = :id"),
                     {"id": row["id"]})
