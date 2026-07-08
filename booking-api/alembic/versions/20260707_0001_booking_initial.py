"""booking initial migration

Revision ID: 20260707_0001
Revises:
Create Date: 2026-07-07 00:00:00.000000

Creates all booking-api tables:
  - meeting_rooms
  - bookings  (+ btree_gist no_double_booking exclusion constraint)
  - booking_notification_log
  - booking_audit_log
  - booking_config

Does NOT touch the `users` table (mirror only; owned by epms-api).
"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260707_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── meeting_rooms ────────────────────────────────────────────────────────
    op.create_table(
        "meeting_rooms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("campus", sa.String(128), nullable=True),
        sa.Column("building", sa.String(128), nullable=True),
        sa.Column("floor", sa.String(64), nullable=True),
        sa.Column("area", sa.String(128), nullable=True),
        sa.Column("capacity", sa.Integer, nullable=False),
        sa.Column("equipment", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("room_type", sa.String(32), nullable=False, server_default="standard"),
        sa.Column("open_time_start", sa.Time, nullable=True),
        sa.Column("open_time_end", sa.Time, nullable=True),
        sa.Column("advance_booking_days", sa.Integer, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="available"),
        sa.Column("owner_department", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("image_file_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("capacity > 0", name="ck_room_capacity_positive"),
        sa.UniqueConstraint("code", name="uq_meeting_rooms_code"),
    )

    # ── bookings ─────────────────────────────────────────────────────────────
    op.create_table(
        "bookings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("room_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meeting_rooms.id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("organizer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attendee_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="confirmed"),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rrule", sa.Text, nullable=True),
        sa.Column("calendar_uid", sa.String(255), nullable=False),
        sa.Column("ical_sequence", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sync_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_bookings_room_start", "bookings", ["room_id", "starts_at"])
    op.create_index("ix_bookings_series", "bookings", ["series_id"])
    op.create_index("ix_bookings_organizer", "bookings", ["organizer_id"])

    # Exclusion constraint: prevent double-booking of confirmed reservations.
    # Requires the btree_gist extension (ships with standard Postgres).
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        """
        ALTER TABLE bookings ADD CONSTRAINT no_double_booking
        EXCLUDE USING gist (room_id WITH =, tstzrange(starts_at, ends_at) WITH &&)
        WHERE (status = 'confirmed')
        """
    )
    op.execute(
        "ALTER TABLE bookings ADD CONSTRAINT ck_booking_times CHECK (ends_at > starts_at)"
    )

    # ── booking_notification_log ─────────────────────────────────────────────
    op.create_table(
        "booking_notification_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bookings.id"), nullable=True),
        sa.Column("notif_type", sa.String(16), nullable=False),
        sa.Column("recipients", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── booking_audit_log ────────────────────────────────────────────────────
    op.create_table(
        "booking_audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bookings.id"), nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("before", postgresql.JSONB, nullable=True),
        sa.Column("after", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── booking_config ───────────────────────────────────────────────────────
    op.create_table(
        "booking_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("smtp_settings", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("rules", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("organizer_mode", sa.String(16), nullable=False, server_default="system"),
    )


def downgrade() -> None:
    op.drop_table("booking_config")
    op.drop_table("booking_audit_log")
    op.drop_table("booking_notification_log")
    op.drop_index("ix_bookings_organizer", table_name="bookings")
    op.drop_index("ix_bookings_series", table_name="bookings")
    op.drop_index("ix_bookings_room_start", table_name="bookings")
    op.drop_table("bookings")
    op.drop_table("meeting_rooms")
    # NOTE: btree_gist extension is intentionally NOT dropped — other services
    # may depend on it, and extensions are DB-wide, not schema-scoped.
