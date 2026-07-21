"""Pydantic schemas for Company Config."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── Sub-config schemas ──────────────────────────────────────────────────────

class WorkflowConfig(BaseModel):
    escalation_threshold_cad: float = 60000
    po_low_value_bypass_enabled: bool = False
    po_low_value_bypass_cad: float = 5000
    approval_reminder_days: int = 2
    approval_auto_escalation_days: int = 5
    # NOTE: `over_budget_mode` is the single source of truth on
    # BudgetAdminConfig (admin panel → Budget Config). Do NOT re-add it here.
    consolidate_gm_opm_approval: bool = True


class PdfTemplateSettings(BaseModel):
    show_logo: bool = True
    header_note: str = ""
    footer_note: str = ""
    show_terms: bool = False
    terms_text: str = ""


class PdfTemplates(BaseModel):
    pr: PdfTemplateSettings = PdfTemplateSettings()
    po: PdfTemplateSettings = PdfTemplateSettings()
    gr: PdfTemplateSettings = PdfTemplateSettings()
    pa: PdfTemplateSettings = PdfTemplateSettings()


class ServiceGrSlaConfig(BaseModel):
    reminder_days: int = 1
    manager_escalation_days: int = 3
    gm_opm_escalation_days: int = 5
    fm_alert_days: int = 7


class GrNotificationSlaConfig(BaseModel):
    reminder_days: int = 1
    manager_escalation_days: int = 3


class PrepaymentConfig(BaseModel):
    max_prepayment_pct: float = 100
    settlement_sla_days: int = 5
    settlement_manager_escalation_days: int = 3
    settlement_gm_opm_escalation_days: int = 5
    block_po_closure_on_unsettled: bool = True


class BudgetAdminConfig(BaseModel):
    yellow_threshold_pct: float = 80
    red_threshold_pct: float = 100
    over_budget_mode: str = "fm_gm_opm"   # fm_gm_opm | fm_only | hard_block
    # Fiscal years selectable in "New Budget Plan" / plan filter dropdowns.
    # Edited in Portal → Budget Config so finance can add e.g. 2028 in advance
    # without a code change. Stored as a sorted list of distinct integers.
    # NB: The system assumes fiscal year == calendar year. Date→fiscal-year
    # derivation across services uses `date.year` directly — there are no
    # configurable start/end month boundaries.
    available_fiscal_years: list[int] = [2024, 2025, 2026, 2027]


class CollectionConfig(BaseModel):
    collection_required: bool = True
    reminder_days: int = 2
    manager_escalation_days: int = 4
    fm_alert_days: int = 7


class WorkflowNodeDef(BaseModel):
    id: str
    label: str
    role: str


class WorkflowDefs(BaseModel):
    pr: list[WorkflowNodeDef] = []
    po: list[WorkflowNodeDef] = []
    pa: list[WorkflowNodeDef] = []


# ── Custom Roles ─────────────────────────────────────────────────────────────

class CustomRoleCreate(BaseModel):
    code: str = Field(pattern=r'^[a-z][a-z0-9_]{1,48}[a-z0-9]$', description="snake_case role code, immutable after creation")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)


class CustomRoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class CustomRoleResponse(BaseModel):
    code: str
    name: str
    description: str
    is_active: bool


# ── Role Permissions ─────────────────────────────────────────────────────────

class RolePermissionsUpdate(BaseModel):
    """Map of role_code → {permission_key: bool}.  Only supplied roles/perms are updated."""
    permissions: dict[str, dict[str, bool]]


# ── Main config ─────────────────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    """All fields optional — PATCH semantics."""
    name: str | None = None
    tagline: str | None = None
    module_taglines: dict[str, str] | None = None
    logo_data_url: str | None = None
    logo_file_name: str | None = None
    delivery_address: str | None = None

    default_currency: str | None = None
    enabled_currencies: list[str] | None = None
    custom_currencies: list[dict[str, Any]] | None = None

    vendor_categories: list[str] | None = None

    mfa_enabled: bool | None = None
    password_expiry_days: int | None = None

    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool | None = None
    smtp_from: str | None = None

    # PO-to-vendor SMTP (falls back to smtp_* above when unset).
    po_smtp_host: str | None = None
    po_smtp_port: int | None = None
    po_smtp_user: str | None = None
    po_smtp_password: str | None = None
    po_smtp_use_tls: bool | None = None
    po_smtp_from: str | None = None

    po_email_subject: str | None = None
    po_email_body: str | None = None
    pdf_templates: dict[str, Any] | None = None

    workflow_config: dict[str, Any] | None = None
    # dept_gm_opm_mapping / dept_supervisor_enabled / dept_director_mapping
    # write paths all retired now (2026-07-16, approval routing phase 3 Task
    # 3b): expense-api's invoice_list.py — the last live consumer of
    # dept_gm_opm_mapping — switched to approval-api's approval_dept_routing
    # in b2f7dc8, so nothing reads any of these three JSONBs anymore outside
    # epms-api's own frozen-snapshot comments and the PMS import tool's
    # reconstruct.py snapshot read. Columns + read schema (ConfigResponse)
    # kept for rollback / reconstruct.py per Global Constraints — just no
    # longer writable via the admin API.
    service_gr_sla: dict[str, Any] | None = None
    gr_notification_sla: dict[str, Any] | None = None
    prepayment_config: dict[str, Any] | None = None
    budget_admin_config: dict[str, Any] | None = None
    collection_config: dict[str, Any] | None = None
    role_management: dict[str, Any] | None = None
    workflow_defs: dict[str, Any] | None = None
    role_permissions: dict[str, Any] | None = None
    custom_roles: list[dict[str, Any]] | None = None
    email_templates: dict[str, Any] | None = None
    notification_settings: dict[str, Any] | None = None


class ConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    tagline: str
    module_taglines: dict[str, Any]
    logo_data_url: str | None
    logo_file_name: str | None
    delivery_address: str

    default_currency: str
    enabled_currencies: list[str]
    custom_currencies: list[dict[str, Any]]

    vendor_categories: list[str]

    mfa_enabled: bool
    password_expiry_days: int | None

    # 3-way match tolerance (percent). Exposed as float so the frontend gets a
    # clean JSON number (Decimal would serialize as a string). Drives the invoice
    # match pass/exception UI so it stops hardcoding 5%.
    invoice_match_tolerance_pct: float = 5.0

    smtp_host: str | None
    smtp_port: int | None
    smtp_user: str | None
    smtp_password: str | None
    smtp_use_tls: bool | None
    smtp_from: str | None

    po_smtp_host: str | None = None
    po_smtp_port: int | None = None
    po_smtp_user: str | None = None
    po_smtp_password: str | None = None
    po_smtp_use_tls: bool | None = None
    po_smtp_from: str | None = None

    po_email_subject: str
    po_email_body: str
    pdf_templates: dict[str, Any]

    workflow_config: dict[str, Any]
    dept_gm_opm_mapping: dict[str, Any]
    dept_supervisor_enabled: dict[str, Any]
    dept_director_mapping: dict[str, Any]
    service_gr_sla: dict[str, Any]
    gr_notification_sla: dict[str, Any]
    prepayment_config: dict[str, Any]
    budget_admin_config: dict[str, Any]
    collection_config: dict[str, Any]
    role_management: dict[str, Any]
    workflow_defs: dict[str, Any]
    role_permissions: dict[str, Any]
    custom_roles: list[dict[str, Any]]
    email_templates: dict[str, Any]
    notification_settings: dict[str, Any]

    updated_at: datetime
    updated_by: uuid.UUID | None
