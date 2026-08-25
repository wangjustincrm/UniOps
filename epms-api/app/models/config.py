"""ORM model for Company Config."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CompanyConfig(Base):
    """Singleton row holding all company / workflow configuration."""

    __tablename__ = "company_config"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Basic info ──────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="EPMS")
    tagline: Mapped[str] = mapped_column(
        String(500), nullable=False, default="Enterprise Procurement Management"
    )
    logo_data_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_address: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── Per-module taglines ─────────────────────────────────────────────────
    # `tagline` above is the Portal tagline. This map holds the OTHER modules:
    # {"epms": "...", "oa": "...", "vms": "..."}. Adding a future module = a new
    # key here (no migration). Blank/missing → caller falls back to `tagline`.
    module_taglines: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    # ── Currency ────────────────────────────────────────────────────────────
    default_currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    enabled_currencies: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=lambda: ["CAD", "USD", "EUR", "CNY"]
    )
    custom_currencies: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # ── 3-way match tolerance (Phase a A2, FIN-AP-001) ──────────────────────
    # |variance_pct| <= tolerance → auto-matched (variance still recorded);
    # above → exception for review. Company default is 5% (a small over-invoice
    # within 5% auto-matches instead of raising an exception for review).
    invoice_match_tolerance_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("5"), server_default="5"
    )

    # ── NC purchase sync ────────────────────────────────────────────────────
    # Cutover: only NC POs with order date >= this are imported. 'YYYY-MM-DD
    # HH:MM:SS' (or date). NULL falls back to the env/default in the sync service.
    nc_purchase_cutover: Mapped[str | None] = mapped_column(String(19), nullable=True)
    # Minutes between automatic NC purchase syncs. NULL = nobody has chosen,
    # which resolves to the default in app/tasks/nc_purchase_sync_scheduler.py;
    # 0 = the schedule is off and the Admin button is the only trigger. Kept
    # nullable on purpose so "unset" stays distinguishable from a chosen value
    # (migration ai01 explains why).
    nc_purchase_sync_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # JV(凭证)与 ERP 主数据同步的间隔,语义与上面那列完全一致:
    # NULL=回落默认 / 0=关闭 / >0=分钟数。分别由 finance-api 和 mdm-api 的
    # 调度循环读取(它们各有一个 company_config 的 mirror 模型)。
    nc_jv_sync_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    erp_mdm_sync_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Security ────────────────────────────────────────────────────────────
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    password_expiry_days: Mapped[int | None] = mapped_column(Integer, nullable=True, default=90)

    # ── SMTP (internal task notifications) — overrides .env when set ────────
    # Used for: approval emails, task reminders, MFA OTP. Configured in
    # Portal → Admin → Notification Settings (moved out of Security 2026-05-28).
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_use_tls: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    smtp_from: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── PO SMTP (vendor-facing PO emails) ───────────────────────────────────
    # Optional second SMTP profile used ONLY when sending a Purchase Order
    # email to an external vendor (POST /po/{id}/place-order). When any field
    # is NULL the sender falls back to the internal smtp_* above. Configured
    # in EPMS → Admin → Email Settings.
    po_smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    po_smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    po_smtp_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    po_smtp_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    po_smtp_use_tls: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    po_smtp_from: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Email / PDF templates ───────────────────────────────────────────────
    po_email_subject: Mapped[str] = mapped_column(
        String(500), nullable=False, default="Purchase Order {po_number} from {company_name}"
    )
    po_email_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    pdf_templates: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # ── Workflow & SLA config (all stored as JSONB objects) ─────────────────
    workflow_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dept_gm_opm_mapping: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dept_director_mapping: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dept_supervisor_enabled: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    service_gr_sla: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    gr_notification_sla: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    prepayment_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    budget_admin_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    collection_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Remittance advice: {enabled, from_email, from_name, cc_email,
    # smtp_user, smtp_password}. Deliberately a JSONB blob defaulting to {}
    # so every consumer reads through .get() with an explicit fallback — a
    # non-null scalar default would shadow the switch the way
    # notification_channel shadowed default_channel.
    remittance_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    role_management: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    workflow_defs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    role_permissions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    custom_roles: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    email_templates: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    notification_settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # ── Vendor settings ─────────────────────────────────────────────────────
    vendor_categories: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=lambda: [
            "Raw Materials", "IT", "Services", "Office Supplies", "Maintenance", "Logistics"
        ]
    )

    # ── Audit ───────────────────────────────────────────────────────────────
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
