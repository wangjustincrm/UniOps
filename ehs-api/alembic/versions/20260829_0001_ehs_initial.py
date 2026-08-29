"""Safety module: initial schema.

Creates the 23 tables ehs-api owns. It deliberately does NOT create `users`,
`tasks`, `departments` or `locations` — those belong to epms-api and mdm-api.
ehs-api declares read-only (and, for `tasks`, read+write) mirrors of them so
SQLAlchemy can resolve foreign keys and the test suite can materialize a
working schema, but creating or altering them from here would put two services
in charge of one table. Alembic's autogenerate proposed all four; they were
removed by hand, the same treatment booking-api and vms-api gave their mirrors.

Every NOT NULL column that has a sensible default carries it as a
server_default, not only as a Python-side default. An ORM insert honours
either, but bulk_insert, raw SQL and a later ALTER TABLE only see the server
side — the seed migration that follows this one inserts the config row without
listing its columns, and would fail against Python-only defaults.

`locations` must already exist, so mdm-api migration 0018_locations runs first.
See docs/superpowers/specs/2026-08-29-ehs-safety-phase1-design.md §12.1.

Revision ID: 20260829_0001
Revises:
Create Date: 2026-08-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20260829_0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('ehs_communications',
    sa.Column('doc_type', sa.String(length=20), nullable=True),
    sa.Column('doc_id', sa.UUID(), nullable=True),
    sa.Column('direction', sa.String(length=3), nullable=False),
    sa.Column('from_addr', sa.String(length=320), nullable=False),
    sa.Column('to_addrs', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('cc_addrs', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('subject', sa.String(length=500), nullable=True),
    sa.Column('body_text', sa.Text(), nullable=True),
    sa.Column('body_html', sa.Text(), nullable=True),
    sa.Column('message_id', sa.String(length=255), nullable=True),
    sa.Column('in_reply_to', sa.String(length=255), nullable=True),
    sa.Column('is_auto_reply', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('attachments', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('template_key', sa.String(length=60), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('message_id', name='uq_ehs_comm_message_id')
    )
    op.create_index(op.f('ix_ehs_communications_doc_id'), 'ehs_communications', ['doc_id'], unique=False)
    op.create_index(op.f('ix_ehs_communications_doc_type'), 'ehs_communications', ['doc_type'], unique=False)
    op.create_index(op.f('ix_ehs_communications_in_reply_to'), 'ehs_communications', ['in_reply_to'], unique=False)
    op.create_index(op.f('ix_ehs_communications_message_id'), 'ehs_communications', ['message_id'], unique=False)
    op.create_table('ehs_config',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('capa_remind_before_days', sa.Integer(), server_default='3', nullable=False),
    sa.Column('capa_escalate_supervisor_days', sa.Integer(), server_default='5', nullable=False),
    sa.Column('capa_escalate_manager_days', sa.Integer(), server_default='10', nullable=False),
    sa.Column('cert_warn_days', postgresql.JSONB(astext_type=sa.Text()), server_default='[90, 60, 30]', nullable=False),
    sa.Column('allow_anonymous_report', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('statutory_scan_interval_minutes', sa.Integer(), server_default='60', nullable=False),
    sa.Column('incident_notify_groups', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('email_templates', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('lost_time_rules', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('id = 1', name='ck_ehs_config_singleton'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('ehs_courses',
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_statutory', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('applies_to_all', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('validity_months', sa.Integer(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_index(op.f('ix_ehs_courses_is_statutory'), 'ehs_courses', ['is_statutory'], unique=False)
    op.create_table('ehs_holidays',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('year', sa.SmallInteger(), nullable=False),
    sa.Column('holiday_date', sa.Date(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('holiday_date', name='uq_ehs_holiday_date')
    )
    op.create_index(op.f('ix_ehs_holidays_year'), 'ehs_holidays', ['year'], unique=False)
    op.create_table('ehs_job_positions',
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('ehs_vocabularies',
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_hierarchical', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_system_locked', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('attr_schema', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_table('ehs_incidents',
    sa.Column('incident_no', sa.String(length=20), nullable=False),
    sa.Column('form_kind', sa.String(length=20), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('reported_by', sa.UUID(), nullable=True),
    sa.Column('reported_by_name', sa.String(length=255), nullable=True),
    sa.Column('is_anonymous', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('reported_to_id', sa.UUID(), nullable=True),
    sa.Column('reported_to_name', sa.String(length=255), nullable=True),
    sa.Column('injury_class', sa.String(length=15), nullable=True),
    sa.Column('mol_reportable', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('mol_reportable_reason', sa.String(length=40), nullable=True),
    sa.Column('classified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('classified_by', sa.UUID(), nullable=True),
    sa.Column('classified_by_name', sa.String(length=255), nullable=True),
    sa.Column('employer_aware_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('equipment_involved', sa.Text(), nullable=True),
    sa.Column('witnesses_note', sa.Text(), nullable=True),
    sa.Column('immediate_action_taken', sa.Text(), nullable=True),
    sa.Column('extra', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('approval_status', sa.String(length=20), nullable=True),
    sa.Column('approval_step_idx', sa.Integer(), nullable=True),
    sa.Column('signature', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('location_id', sa.UUID(), nullable=True),
    sa.Column('location_path', sa.String(length=500), nullable=True),
    sa.Column('department_id', sa.UUID(), nullable=True),
    sa.Column('shift_code', sa.String(length=20), nullable=True),
    sa.Column('category_id', sa.UUID(), nullable=True),
    sa.Column('category_label', sa.String(length=200), nullable=True),
    sa.Column('severity', sa.String(length=10), nullable=True),
    sa.Column('owner_name', sa.String(length=255), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='draft', nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['classified_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['reported_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['reported_to_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ehs_incidents_department_id'), 'ehs_incidents', ['department_id'], unique=False)
    op.create_index(op.f('ix_ehs_incidents_form_kind'), 'ehs_incidents', ['form_kind'], unique=False)
    op.create_index(op.f('ix_ehs_incidents_incident_no'), 'ehs_incidents', ['incident_no'], unique=True)
    op.create_index(op.f('ix_ehs_incidents_injury_class'), 'ehs_incidents', ['injury_class'], unique=False)
    op.create_index(op.f('ix_ehs_incidents_location_id'), 'ehs_incidents', ['location_id'], unique=False)
    op.create_index(op.f('ix_ehs_incidents_mol_reportable'), 'ehs_incidents', ['mol_reportable'], unique=False)
    op.create_index(op.f('ix_ehs_incidents_occurred_at'), 'ehs_incidents', ['occurred_at'], unique=False)
    op.create_index(op.f('ix_ehs_incidents_owner_id'), 'ehs_incidents', ['owner_id'], unique=False)
    op.create_index(op.f('ix_ehs_incidents_severity'), 'ehs_incidents', ['severity'], unique=False)
    op.create_index(op.f('ix_ehs_incidents_shift_code'), 'ehs_incidents', ['shift_code'], unique=False)
    op.create_index(op.f('ix_ehs_incidents_status'), 'ehs_incidents', ['status'], unique=False)
    op.create_table('ehs_position_requirements',
    sa.Column('position_id', sa.UUID(), nullable=False),
    sa.Column('requirement_type', sa.String(length=20), nullable=False),
    sa.Column('course_id', sa.UUID(), nullable=True),
    sa.Column('vocabulary_item_id', sa.UUID(), nullable=True),
    sa.Column('label', sa.String(length=200), nullable=True),
    sa.Column('is_mandatory', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['position_id'], ['ehs_job_positions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ehs_position_requirements_position_id'), 'ehs_position_requirements', ['position_id'], unique=False)
    op.create_table('ehs_statutory_deadlines',
    sa.Column('source_type', sa.String(length=20), nullable=False),
    sa.Column('source_id', sa.UUID(), nullable=False),
    sa.Column('source_ref', sa.String(length=60), nullable=True),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('regulation_ref', sa.String(length=60), nullable=True),
    sa.Column('clock_type', sa.String(length=10), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('satisfied_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('satisfied_by', sa.UUID(), nullable=True),
    sa.Column('satisfied_by_name', sa.String(length=255), nullable=True),
    sa.Column('evidence_file_id', sa.UUID(), nullable=True),
    sa.Column('evidence_note', sa.String(length=500), nullable=True),
    sa.Column('escalation_level', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['satisfied_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ehs_deadline_due_open', 'ehs_statutory_deadlines', ['due_at'], unique=False, postgresql_where='satisfied_at IS NULL')
    op.create_index('ix_ehs_deadline_source', 'ehs_statutory_deadlines', ['source_type', 'source_id'], unique=False)
    op.create_index(op.f('ix_ehs_statutory_deadlines_kind'), 'ehs_statutory_deadlines', ['kind'], unique=False)
    op.create_table('ehs_training_records',
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('user_name', sa.String(length=255), nullable=False),
    sa.Column('course_id', sa.UUID(), nullable=True),
    sa.Column('course_label', sa.String(length=200), nullable=False),
    sa.Column('completed_on', sa.Date(), nullable=False),
    sa.Column('expires_on', sa.Date(), nullable=True),
    sa.Column('delivery', sa.String(length=15), server_default='internal', nullable=False),
    sa.Column('certificate_file_id', sa.UUID(), nullable=True),
    sa.Column('recorded_by', sa.UUID(), nullable=True),
    sa.Column('recorded_by_name', sa.String(length=255), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['course_id'], ['ehs_courses.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['recorded_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ehs_training_records_completed_on'), 'ehs_training_records', ['completed_on'], unique=False)
    op.create_index(op.f('ix_ehs_training_records_course_id'), 'ehs_training_records', ['course_id'], unique=False)
    op.create_index(op.f('ix_ehs_training_records_expires_on'), 'ehs_training_records', ['expires_on'], unique=False)
    op.create_index(op.f('ix_ehs_training_records_user_id'), 'ehs_training_records', ['user_id'], unique=False)
    op.create_table('ehs_vocabulary_items',
    sa.Column('vocabulary_code', sa.String(length=40), nullable=False),
    sa.Column('parent_id', sa.UUID(), nullable=True),
    sa.Column('code', sa.String(length=60), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('attrs', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('path', sa.String(length=500), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['ehs_vocabulary_items.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['vocabulary_code'], ['ehs_vocabularies.code'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('vocabulary_code', 'code', name='uq_ehs_vocab_item_code')
    )
    op.create_index(op.f('ix_ehs_vocabulary_items_is_active'), 'ehs_vocabulary_items', ['is_active'], unique=False)
    op.create_index(op.f('ix_ehs_vocabulary_items_parent_id'), 'ehs_vocabulary_items', ['parent_id'], unique=False)
    op.create_index(op.f('ix_ehs_vocabulary_items_vocabulary_code'), 'ehs_vocabulary_items', ['vocabulary_code'], unique=False)
    op.create_table('ehs_worker_certifications',
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('user_name', sa.String(length=255), nullable=False),
    sa.Column('cert_type_id', sa.UUID(), nullable=True),
    sa.Column('cert_type_label', sa.String(length=200), nullable=False),
    sa.Column('cert_no', sa.String(length=80), nullable=True),
    sa.Column('issuer', sa.String(length=200), nullable=True),
    sa.Column('issued_on', sa.Date(), nullable=True),
    sa.Column('expires_on', sa.Date(), nullable=True),
    sa.Column('file_id', sa.UUID(), nullable=True),
    sa.Column('is_blocking', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ehs_worker_certifications_expires_on'), 'ehs_worker_certifications', ['expires_on'], unique=False)
    op.create_index(op.f('ix_ehs_worker_certifications_user_id'), 'ehs_worker_certifications', ['user_id'], unique=False)
    op.create_table('ehs_worker_positions',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('position_id', sa.UUID(), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=False),
    sa.Column('effective_to', sa.Date(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['position_id'], ['ehs_job_positions.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'position_id', 'effective_from', name='uq_ehs_worker_position')
    )
    op.create_index(op.f('ix_ehs_worker_positions_position_id'), 'ehs_worker_positions', ['position_id'], unique=False)
    op.create_index(op.f('ix_ehs_worker_positions_user_id'), 'ehs_worker_positions', ['user_id'], unique=False)
    op.create_table('ehs_worker_profiles',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('employee_no', sa.String(length=30), nullable=True),
    sa.Column('hire_date', sa.Date(), nullable=True),
    sa.Column('employment_type', sa.String(length=20), nullable=True),
    sa.Column('shift_code', sa.String(length=20), nullable=True),
    sa.Column('phone_mobile', sa.String(length=30), nullable=True),
    sa.Column('primary_location_id', sa.UUID(), nullable=True),
    sa.Column('emergency_contact', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('medical_notes', sa.Text(), nullable=True),
    sa.Column('is_safety_sensitive', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('jhsc_role', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id'),
    sa.UniqueConstraint('employee_no')
    )
    op.create_index(op.f('ix_ehs_worker_profiles_shift_code'), 'ehs_worker_profiles', ['shift_code'], unique=False)
    op.create_table('ehs_first_aid_log',
    sa.Column('log_no', sa.String(length=20), nullable=False),
    sa.Column('incident_id', sa.UUID(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('location_id', sa.UUID(), nullable=True),
    sa.Column('location_path', sa.String(length=500), nullable=True),
    sa.Column('injured_user_id', sa.UUID(), nullable=True),
    sa.Column('injured_name', sa.String(length=255), nullable=False),
    sa.Column('first_aider_id', sa.UUID(), nullable=True),
    sa.Column('first_aider_name', sa.String(length=255), nullable=True),
    sa.Column('body_part_id', sa.UUID(), nullable=True),
    sa.Column('body_part_label', sa.String(length=200), nullable=True),
    sa.Column('treatment_given', sa.Text(), nullable=False),
    sa.Column('sent_offsite', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('follow_up', sa.Text(), nullable=True),
    sa.Column('file_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['first_aider_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['incident_id'], ['ehs_incidents.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['injured_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ehs_first_aid_log_incident_id'), 'ehs_first_aid_log', ['incident_id'], unique=False)
    op.create_index(op.f('ix_ehs_first_aid_log_log_no'), 'ehs_first_aid_log', ['log_no'], unique=True)
    op.create_index(op.f('ix_ehs_first_aid_log_occurred_at'), 'ehs_first_aid_log', ['occurred_at'], unique=False)
    op.create_table('ehs_incident_causes',
    sa.Column('incident_id', sa.UUID(), nullable=False),
    sa.Column('cause_type', sa.String(length=12), nullable=False),
    sa.Column('vocabulary_item_id', sa.UUID(), nullable=True),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['ehs_incidents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ehs_incident_causes_incident_id'), 'ehs_incident_causes', ['incident_id'], unique=False)
    op.create_table('ehs_incident_investigations',
    sa.Column('incident_id', sa.UUID(), nullable=False),
    sa.Column('investigator_id', sa.UUID(), nullable=True),
    sa.Column('investigator_name', sa.String(length=255), nullable=True),
    sa.Column('supporting_investigator_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('identified_hazard_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('ppe_that_could_prevent', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('sequence_of_events', sa.Text(), nullable=True),
    sa.Column('root_cause_narrative', sa.Text(), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('signed_by', sa.UUID(), nullable=True),
    sa.Column('signed_by_name', sa.String(length=255), nullable=True),
    sa.Column('signature', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['ehs_incidents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['investigator_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['signed_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ehs_incident_investigations_incident_id'), 'ehs_incident_investigations', ['incident_id'], unique=True)
    op.create_table('ehs_incident_persons',
    sa.Column('incident_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('person_name', sa.String(length=255), nullable=False),
    sa.Column('external_company', sa.String(length=200), nullable=True),
    sa.Column('department_id', sa.UUID(), nullable=True),
    sa.Column('body_part_id', sa.UUID(), nullable=True),
    sa.Column('body_part_label', sa.String(length=200), nullable=True),
    sa.Column('nature_of_injury_id', sa.UUID(), nullable=True),
    sa.Column('nature_of_injury_label', sa.String(length=200), nullable=True),
    sa.Column('treatment', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['ehs_incidents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ehs_incident_persons_incident_id'), 'ehs_incident_persons', ['incident_id'], unique=False)
    op.create_table('ehs_rtw_plans',
    sa.Column('incident_id', sa.UUID(), nullable=False),
    sa.Column('worker_user_id', sa.UUID(), nullable=True),
    sa.Column('worker_name', sa.String(length=255), nullable=False),
    sa.Column('functional_abilities', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('restrictions', sa.Text(), nullable=True),
    sa.Column('modified_duties', sa.Text(), nullable=True),
    sa.Column('at_regular_pay', sa.Boolean(), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('expected_end_date', sa.Date(), nullable=True),
    sa.Column('actual_end_date', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='active', nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['ehs_incidents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['worker_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ehs_rtw_plans_incident_id'), 'ehs_rtw_plans', ['incident_id'], unique=False)
    op.create_table('ehs_actions',
    sa.Column('action_no', sa.String(length=20), nullable=False),
    sa.Column('source_type', sa.String(length=20), nullable=False),
    sa.Column('source_id', sa.UUID(), nullable=True),
    sa.Column('source_ref', sa.String(length=60), nullable=True),
    sa.Column('cause_id', sa.UUID(), nullable=True),
    sa.Column('action_type', sa.String(length=15), server_default='corrective', nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('hierarchy_of_control', sa.String(length=20), nullable=True),
    sa.Column('priority', sa.String(length=10), server_default='normal', nullable=False),
    sa.Column('due_date', sa.Date(), nullable=False),
    sa.Column('escalation_level', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('last_escalated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('location_id', sa.UUID(), nullable=True),
    sa.Column('location_path', sa.String(length=500), nullable=True),
    sa.Column('department_id', sa.UUID(), nullable=True),
    sa.Column('shift_code', sa.String(length=20), nullable=True),
    sa.Column('category_id', sa.UUID(), nullable=True),
    sa.Column('category_label', sa.String(length=200), nullable=True),
    sa.Column('severity', sa.String(length=10), nullable=True),
    sa.Column('owner_name', sa.String(length=255), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='open', nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("source_type IN ('incident', 'inspection', 'jhsc', 'audit', 'observation', 'hazard', 'drill', 'permit', 'manual')", name='ck_ehs_action_source_type'),
    sa.ForeignKeyConstraint(['cause_id'], ['ehs_incident_causes.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ehs_actions_action_no'), 'ehs_actions', ['action_no'], unique=True)
    op.create_index(op.f('ix_ehs_actions_cause_id'), 'ehs_actions', ['cause_id'], unique=False)
    op.create_index(op.f('ix_ehs_actions_department_id'), 'ehs_actions', ['department_id'], unique=False)
    op.create_index(op.f('ix_ehs_actions_due_date'), 'ehs_actions', ['due_date'], unique=False)
    op.create_index(op.f('ix_ehs_actions_hierarchy_of_control'), 'ehs_actions', ['hierarchy_of_control'], unique=False)
    op.create_index(op.f('ix_ehs_actions_location_id'), 'ehs_actions', ['location_id'], unique=False)
    op.create_index(op.f('ix_ehs_actions_occurred_at'), 'ehs_actions', ['occurred_at'], unique=False)
    op.create_index(op.f('ix_ehs_actions_owner_id'), 'ehs_actions', ['owner_id'], unique=False)
    op.create_index(op.f('ix_ehs_actions_severity'), 'ehs_actions', ['severity'], unique=False)
    op.create_index(op.f('ix_ehs_actions_shift_code'), 'ehs_actions', ['shift_code'], unique=False)
    op.create_index(op.f('ix_ehs_actions_source_id'), 'ehs_actions', ['source_id'], unique=False)
    op.create_index(op.f('ix_ehs_actions_source_type'), 'ehs_actions', ['source_type'], unique=False)
    op.create_index(op.f('ix_ehs_actions_status'), 'ehs_actions', ['status'], unique=False)
    op.create_table('ehs_rtw_checkins',
    sa.Column('plan_id', sa.UUID(), nullable=False),
    sa.Column('checkin_date', sa.Date(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('by_user_id', sa.UUID(), nullable=True),
    sa.Column('by_name', sa.String(length=255), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['by_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['plan_id'], ['ehs_rtw_plans.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ehs_rtw_checkins_plan_id'), 'ehs_rtw_checkins', ['plan_id'], unique=False)
    op.create_table('ehs_action_updates',
    sa.Column('action_id', sa.UUID(), nullable=False),
    sa.Column('author_id', sa.UUID(), nullable=True),
    sa.Column('author_name', sa.String(length=255), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('file_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('new_status', sa.String(length=20), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['action_id'], ['ehs_actions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ehs_action_updates_action_id'), 'ehs_action_updates', ['action_id'], unique=False)
    op.create_table('ehs_action_verifications',
    sa.Column('action_id', sa.UUID(), nullable=False),
    sa.Column('verified_by', sa.UUID(), nullable=True),
    sa.Column('verified_by_name', sa.String(length=255), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('is_effective', sa.Boolean(), nullable=False),
    sa.Column('evidence', sa.Text(), nullable=True),
    sa.Column('file_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['action_id'], ['ehs_actions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['verified_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ehs_action_verifications_action_id'), 'ehs_action_verifications', ['action_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ehs_action_verifications_action_id'), table_name='ehs_action_verifications')
    op.drop_table('ehs_action_verifications')
    op.drop_index(op.f('ix_ehs_action_updates_action_id'), table_name='ehs_action_updates')
    op.drop_table('ehs_action_updates')
    op.drop_index(op.f('ix_ehs_rtw_checkins_plan_id'), table_name='ehs_rtw_checkins')
    op.drop_table('ehs_rtw_checkins')
    op.drop_index(op.f('ix_ehs_actions_status'), table_name='ehs_actions')
    op.drop_index(op.f('ix_ehs_actions_source_type'), table_name='ehs_actions')
    op.drop_index(op.f('ix_ehs_actions_source_id'), table_name='ehs_actions')
    op.drop_index(op.f('ix_ehs_actions_shift_code'), table_name='ehs_actions')
    op.drop_index(op.f('ix_ehs_actions_severity'), table_name='ehs_actions')
    op.drop_index(op.f('ix_ehs_actions_owner_id'), table_name='ehs_actions')
    op.drop_index(op.f('ix_ehs_actions_occurred_at'), table_name='ehs_actions')
    op.drop_index(op.f('ix_ehs_actions_location_id'), table_name='ehs_actions')
    op.drop_index(op.f('ix_ehs_actions_hierarchy_of_control'), table_name='ehs_actions')
    op.drop_index(op.f('ix_ehs_actions_due_date'), table_name='ehs_actions')
    op.drop_index(op.f('ix_ehs_actions_department_id'), table_name='ehs_actions')
    op.drop_index(op.f('ix_ehs_actions_cause_id'), table_name='ehs_actions')
    op.drop_index(op.f('ix_ehs_actions_action_no'), table_name='ehs_actions')
    op.drop_table('ehs_actions')
    op.drop_index(op.f('ix_ehs_rtw_plans_incident_id'), table_name='ehs_rtw_plans')
    op.drop_table('ehs_rtw_plans')
    op.drop_index(op.f('ix_ehs_incident_persons_incident_id'), table_name='ehs_incident_persons')
    op.drop_table('ehs_incident_persons')
    op.drop_index(op.f('ix_ehs_incident_investigations_incident_id'), table_name='ehs_incident_investigations')
    op.drop_table('ehs_incident_investigations')
    op.drop_index(op.f('ix_ehs_incident_causes_incident_id'), table_name='ehs_incident_causes')
    op.drop_table('ehs_incident_causes')
    op.drop_index(op.f('ix_ehs_first_aid_log_occurred_at'), table_name='ehs_first_aid_log')
    op.drop_index(op.f('ix_ehs_first_aid_log_log_no'), table_name='ehs_first_aid_log')
    op.drop_index(op.f('ix_ehs_first_aid_log_incident_id'), table_name='ehs_first_aid_log')
    op.drop_table('ehs_first_aid_log')
    op.drop_index(op.f('ix_ehs_worker_profiles_shift_code'), table_name='ehs_worker_profiles')
    op.drop_table('ehs_worker_profiles')
    op.drop_index(op.f('ix_ehs_worker_positions_user_id'), table_name='ehs_worker_positions')
    op.drop_index(op.f('ix_ehs_worker_positions_position_id'), table_name='ehs_worker_positions')
    op.drop_table('ehs_worker_positions')
    op.drop_index(op.f('ix_ehs_worker_certifications_user_id'), table_name='ehs_worker_certifications')
    op.drop_index(op.f('ix_ehs_worker_certifications_expires_on'), table_name='ehs_worker_certifications')
    op.drop_table('ehs_worker_certifications')
    op.drop_index(op.f('ix_ehs_vocabulary_items_vocabulary_code'), table_name='ehs_vocabulary_items')
    op.drop_index(op.f('ix_ehs_vocabulary_items_parent_id'), table_name='ehs_vocabulary_items')
    op.drop_index(op.f('ix_ehs_vocabulary_items_is_active'), table_name='ehs_vocabulary_items')
    op.drop_table('ehs_vocabulary_items')
    op.drop_index(op.f('ix_ehs_training_records_user_id'), table_name='ehs_training_records')
    op.drop_index(op.f('ix_ehs_training_records_expires_on'), table_name='ehs_training_records')
    op.drop_index(op.f('ix_ehs_training_records_course_id'), table_name='ehs_training_records')
    op.drop_index(op.f('ix_ehs_training_records_completed_on'), table_name='ehs_training_records')
    op.drop_table('ehs_training_records')
    op.drop_index(op.f('ix_ehs_statutory_deadlines_kind'), table_name='ehs_statutory_deadlines')
    op.drop_index('ix_ehs_deadline_source', table_name='ehs_statutory_deadlines')
    op.drop_index('ix_ehs_deadline_due_open', table_name='ehs_statutory_deadlines', postgresql_where='satisfied_at IS NULL')
    op.drop_table('ehs_statutory_deadlines')
    op.drop_index(op.f('ix_ehs_position_requirements_position_id'), table_name='ehs_position_requirements')
    op.drop_table('ehs_position_requirements')
    op.drop_index(op.f('ix_ehs_incidents_status'), table_name='ehs_incidents')
    op.drop_index(op.f('ix_ehs_incidents_shift_code'), table_name='ehs_incidents')
    op.drop_index(op.f('ix_ehs_incidents_severity'), table_name='ehs_incidents')
    op.drop_index(op.f('ix_ehs_incidents_owner_id'), table_name='ehs_incidents')
    op.drop_index(op.f('ix_ehs_incidents_occurred_at'), table_name='ehs_incidents')
    op.drop_index(op.f('ix_ehs_incidents_mol_reportable'), table_name='ehs_incidents')
    op.drop_index(op.f('ix_ehs_incidents_location_id'), table_name='ehs_incidents')
    op.drop_index(op.f('ix_ehs_incidents_injury_class'), table_name='ehs_incidents')
    op.drop_index(op.f('ix_ehs_incidents_incident_no'), table_name='ehs_incidents')
    op.drop_index(op.f('ix_ehs_incidents_form_kind'), table_name='ehs_incidents')
    op.drop_index(op.f('ix_ehs_incidents_department_id'), table_name='ehs_incidents')
    op.drop_table('ehs_incidents')
    op.drop_table('ehs_vocabularies')
    op.drop_table('ehs_job_positions')
    op.drop_index(op.f('ix_ehs_holidays_year'), table_name='ehs_holidays')
    op.drop_table('ehs_holidays')
    op.drop_index(op.f('ix_ehs_courses_is_statutory'), table_name='ehs_courses')
    op.drop_table('ehs_courses')
    op.drop_table('ehs_config')
    op.drop_index(op.f('ix_ehs_communications_message_id'), table_name='ehs_communications')
    op.drop_index(op.f('ix_ehs_communications_in_reply_to'), table_name='ehs_communications')
    op.drop_index(op.f('ix_ehs_communications_doc_type'), table_name='ehs_communications')
    op.drop_index(op.f('ix_ehs_communications_doc_id'), table_name='ehs_communications')
    op.drop_table('ehs_communications')
