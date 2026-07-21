"""The system-wide task notifier deep-links each document_type to its own module."""
from app.core.config import settings
from app.services.notification import _task_link


def test_task_link_routes_each_module(monkeypatch):
    monkeypatch.setattr(settings, "EPMS_URL", "https://epms.example.com")
    monkeypatch.setattr(settings, "OA_URL", "https://oa.example.com")
    monkeypatch.setattr(settings, "FINANCE_URL", "https://finance.example.com")
    monkeypatch.setattr(settings, "VMS_URL", "https://vms.example.com")
    i = "abc123"

    # EPMS
    assert _task_link("pr", i) == "https://epms.example.com/pr/abc123"
    assert _task_link("po", i) == "https://epms.example.com/po/abc123"
    assert _task_link("pa", i) == "https://epms.example.com/pa/abc123"
    assert _task_link("gr", i) == "https://epms.example.com/gr/abc123"
    assert _task_link("invoice", i) == "https://epms.example.com/invoices/abc123"
    # OA Direct PA
    assert _task_link("pa_dir", i) == "https://oa.example.com/pa/abc123"
    # Finance budget plan
    assert _task_link("budget_plan", i) == "https://finance.example.com/budget/plans/abc123"
    # VMS routes from its root
    assert _task_link("vms_visit", i) == "https://vms.example.com"
    assert _task_link("vms_train", i) == "https://vms.example.com"
    # OA expense claims (claim_type.lower(), open-ended incl custom forms) default to OA /expenses
    assert _task_link("expense", i) == "https://oa.example.com/expenses/abc123"
    assert _task_link("trv", i) == "https://oa.example.com/expenses/abc123"
    assert _task_link("mycustomform", i) == "https://oa.example.com/expenses/abc123"
    # case-insensitive on document_type
    assert _task_link("PR", i) == "https://epms.example.com/pr/abc123"
