"""The Communications log: every email, searchable, with reminders (round 12,
COM-01, COM-03 and Q16).

Backfilled for what was filed before: the people the addresses belong to, the
record's number, and the button each message carried (read back out of the
HTML this module wrote). Nothing is purged here; the scheduler does that, on
HSE's retention setting.

Revision ID: 20261111_0102
Revises: 20261111_0101
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261111_0102"
down_revision = "20261111_0101"
branch_labels = None
depends_on = None

# Same answers as app/services/mail.py DOC_NUMBER_SQL, written out so this
# migration does not change when that does.
_NUMBERS = {
    "ehs_inc": "select incident_no from ehs_incidents x where x.id = c.doc_id",
    "ehs_act": "select action_no from ehs_actions x where x.id = c.doc_id",
    "ehs_doc": "select doc_no from ehs_documents x where x.id = c.doc_id",
    "ehs_crs": "select delivery_no from ehs_course_deliveries x where x.id = c.doc_id",
    "ehs_tbt": ("select t.talk_no from ehs_tbt_deliveries d join ehs_toolbox_talks t "
                "on t.id = d.talk_id where d.id = c.doc_id"),
    "ehs_jhsc": ("select coalesce((select meeting_no from ehs_jhsc_meetings m where m.id = c.doc_id),"
                 " (select m.meeting_no || ' #' || r.seq from ehs_jhsc_recommendations r"
                 "  join ehs_jhsc_meetings m on m.id = r.meeting_id where r.id = c.doc_id))"),
}


def upgrade() -> None:
    op.add_column("ehs_communications", sa.Column(
        "to_user_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("ehs_communications", sa.Column("doc_number", sa.String(60), nullable=True))
    op.create_index("ix_ehs_communications_doc_number", "ehs_communications", ["doc_number"])
    op.add_column("ehs_communications", sa.Column("error", sa.Text, nullable=True))
    op.add_column("ehs_communications", sa.Column("link", sa.String(500), nullable=True))
    op.add_column("ehs_communications", sa.Column("link_label", sa.String(80), nullable=True))
    op.add_column("ehs_communications", sa.Column(
        "resend_of", UUID(as_uuid=True),
        sa.ForeignKey("ehs_communications.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_ehs_communications_resend_of", "ehs_communications", ["resend_of"])
    op.add_column("ehs_communications", sa.Column("resent_by_name", sa.String(255), nullable=True))
    op.add_column("ehs_communications", sa.Column(
        "body_purged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ehs_config", sa.Column(
        "communication_retention_days", sa.Integer, nullable=False, server_default="21"))

    bind = op.get_bind()
    people = bind.execute(sa.text("""
        update ehs_communications c set to_user_ids = coalesce((
            select jsonb_agg(distinct u.id::text) from users u
             where lower(u.email) in (select lower(a) from jsonb_array_elements_text(c.to_addrs) a)
        ), '[]'::jsonb)""")).rowcount
    numbered = 0
    for doc_type, sql in _NUMBERS.items():
        numbered += bind.execute(sa.text(
            f"update ehs_communications c set doc_number = ({sql}) "  # noqa: S608
            "where c.doc_type = :t"), {"t": doc_type}).rowcount
    links = bind.execute(sa.text(r"""
        update ehs_communications c
           set link = replace(substring(c.body_html from 'href="([^"]+)"'), '&amp;', '&')
         where c.body_html like '%href="%'""")).rowcount
    print(f"communications: {people} matched to people, {numbered} numbered, {links} links kept")


def downgrade() -> None:
    op.drop_column("ehs_config", "communication_retention_days")
    for col in ("body_purged_at", "resent_by_name", "resend_of", "link_label", "link",
                "error", "doc_number", "to_user_ids"):
        op.drop_column("ehs_communications", col)
