"""Trading partners (design §3.1, FR-OB-03, FR-OB-33): the configuration
screen's use cases. CRUD, CSV import / export and the permission checks come
from the platform's CrudService; this module adds the partner's rules."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.domain.config import CHANNELS, INVOICE_METHODS, SENDING_MODES, SETTLEMENT_MODES
from app.domain.partners import check_partner
from app.models.config import Partner
from app.platform.errors import BusinessError
from app.platform.repository import Repository
from app.platform.services import CrudService
from app.platform.views import Preset
from app.schemas.partners import PartnerCreate, PartnerRead, PartnerUpdate

READ = "sales.read"
CONFIG = "sales.config"


class PartnerRepository(Repository[Partner]):
    model = Partner
    search_fields = ("code", "name", "abbrev")
    list_fields = ("code", "name", "abbrev", "channel", "settlement_mode", "sending_mode", "delay_minutes",
                   "invoice_method", "invoice_email", "payment_terms", "dc_routing_required", "min_shelf_life_days",
                   "label_code", "active", "created_at", "updated_at")
    labels = {"abbrev": "Abbreviation", "delay_minutes": "Delay (minutes)", "dc_routing_required": "DC routing required",
              "min_shelf_life_days": "Min shelf life (days)"}
    enums = {"channel": CHANNELS, "settlement_mode": SETTLEMENT_MODES, "sending_mode": SENDING_MODES,
             "invoice_method": INVOICE_METHODS}
    default_sort = ("code",)
    groupable = ("channel", "settlement_mode", "sending_mode", "invoice_method", "dc_routing_required", "active")
    presets = (Preset("active", "Active", {"filters": [{"field": "active", "op": "eq", "value": "true"}]}),
               Preset("inactive", "Inactive", {"filters": [{"field": "active", "op": "eq", "value": "false"}]}),
               Preset("all", "All"))


# The columns the partner rules look at (check_partner).
_RULE_FIELDS = ("invoice_method", "invoice_email")


class PartnerService(CrudService[Partner, PartnerRead, PartnerCreate, PartnerUpdate]):
    repo_cls = PartnerRepository
    read_schema = PartnerRead
    create_schema = PartnerCreate
    update_schema = PartnerUpdate
    read_permission = READ
    write_permission = CONFIG
    csv_key = "code"
    unique_constraints = {"uq_sales_partners_code_ci": ("partner.code_taken", "code"),
                          "uq_sales_partners_abbrev": ("partner.abbrev_taken", "abbrev")}
    csv_columns = (
        "code", "name", "abbrev", "channel", "settlement_mode", "sending_mode", "delay_minutes",
        "invoice_method", "invoice_email", "invoice_cc", "payment_terms", "dc_routing_required",
        "min_shelf_life_days", "label_code", "carrier_defaults", "active",
    )

    async def validate_create(self, data: PartnerCreate) -> None:
        check_partner(data.model_dump())
        # Codes are compared as the CSV import compares them: "abc " is "ABC".
        taken = (await self.uow.session.execute(
            select(Partner.code).where(func.lower(Partner.code) == data.code.lower())
        )).scalar_one_or_none()
        if taken is not None:
            raise BusinessError(
                "partner.code_taken", f"partner code {taken!r} already exists",
                {"field": "code", "existing": taken},
            )

    async def before_update(self, entity: Partner, changes: dict[str, Any]) -> None:
        check_partner({**{k: getattr(entity, k) for k in _RULE_FIELDS}, **changes})
        if changes.get("abbrev"):  # its "needs an abbreviation" task is done (Order refs, 2026-11-03)
            from app.services.problems import PARTNER_NO_ABBREV, task_service  # problems → models: imported when used
            await task_service(lambda: False).resolve_problem(  # a resolve sends no mail: notify is not read
                self.uow.session, PARTNER_NO_ABBREV, entity.id, by=self.uow.actor.user_id)
