"""Record more, never less (user 2026-11-23): a Core UPDATE / DELETE skips the audit trail (it sees ORM objects only).
Every such statement in app/ is listed here with why it may skip it; a new one fails until it is audited (the ORM, or
platform.audit.audit_also) or listed with its reason."""
import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
CORE_WRITE = re.compile(r"execute\(\s*(?:sa\.)?(update|delete)\(\s*([A-Za-z_][\w.]*)")

ALLOWED = {
    ("services/cycle.py", "update", "t"): "a connection's last run / next run: bookkeeping every run (the run is in sales_runs)",
    ("services/gateway_base.py", "update", "Connection"): "the gateway read cursor: bookkeeping every run",
    ("services/shopify_intake.py", "update", "Connection"): "the Shopify read cursor: bookkeeping every run",
    ("services/gateway_intake.py", "update", "G"): "marks a transaction whose details could not be read (raw.details_failed): retry bookkeeping",
    ("services/stock_counts.py", "delete", "StockMove"): "a count's own moves on Undo: each recorded first with audit_also",
    ("platform/views.py", "update", "m"): "a person's other saved views lose the default flag: a preference, not a record",
    ("integrations/epms_tasks.py", "update", "t"): "the shared tasks table (epms-api's), not a Sales record",
    ("integrations/epms_tasks.py", "update", "self.t"): "the shared tasks table (epms-api's), not a Sales record",
    ("scripts/start_orders_from.py", "delete", "model"): "the admin reset tool (CLI, by hand, with a backup first)",
    ("scripts/start_orders_from.py", "delete", "DocumentLink"): "the admin reset tool (CLI, by hand, with a backup first)",
    ("scripts/import_zoho_history.py", "update", "OutboxRecord"): "the one-time Zoho history replay (CLI)",
    ("scripts/import_zoho_history.py", "update", "Shipment"): "the one-time Zoho history replay (CLI)",
    ("scripts/redact_card_documents.py", "update", "Document.__table__"): "the one-time card-data redaction (CLI)",
    ("platform/jobs.py", "update", "m"): "the job runner's lease / heartbeat on sales_runs: bookkeeping every tick",
}


def _sites():
    for f in APP.rglob("*.py"):
        for m in CORE_WRITE.finditer(f.read_text(encoding="utf-8")):
            yield (f.relative_to(APP).as_posix(), m.group(1), m.group(2))


def test_every_core_update_or_delete_is_audited_or_listed_with_why():
    found = set(_sites())
    assert found - set(ALLOWED) == set(), "audit these (ORM or audit_also), or list them with why"
    assert set(ALLOWED) - found == set(), "listed but gone: take them out of ALLOWED"


def test_the_guard_sees_a_core_write():
    assert CORE_WRITE.search("await s.execute(update(Order).where(Order.id == 1))")
    assert CORE_WRITE.search("await s.execute(sa.delete(StockMove).where(x))")


SECRETISH = re.compile(r"password|secret|token", re.I)


def test_no_audited_model_has_a_secret_column():
    """The audit keeps whole rows (the user: remove nothing), so a secret must never be a column of an audited
    model; a secret's *name* (e.g. secret_name: the env variable) is fine."""
    import app.models  # noqa: F401 — every model mapped
    from app.db.base import Base
    from app.platform.entities import Audited
    bad = [f"{m.class_.__name__}.{c.key}" for m in Base.registry.mappers if issubclass(m.class_, Audited)
           for c in m.columns if SECRETISH.search(c.key) and not c.key.endswith("_name")]
    assert bad == []
