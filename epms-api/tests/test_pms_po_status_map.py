"""PMS 导入 PO 状态映射 — PaymentStatus=PAID 收口为 closed(2026-07-10 用户决策)。"""
from scripts.import_pms.mappings import map_po_status


def test_paid_overrides_open_final_status():
    # 老系统大量已付清但 Status0 仍 OPEN 的 PO — 必须收口为 closed
    assert map_po_status("GM APPROVED", "OPEN", "DELIVERING", "PAID") == "closed"
    assert map_po_status("GM APPROVED", "OPEN", "RECEIVED", "Paid") == "closed"


def test_rejected_and_cancelled_still_win_over_paid():
    assert map_po_status("GM REJECTED", "OPEN", None, "PAID") == "cancelled"
    assert map_po_status("GM APPROVED", "CANCELED", None, "PAID") == "cancelled"


def test_unpaid_paths_unchanged():
    assert map_po_status("GM APPROVED", "OPEN", "DELIVERING", "WAITING INVOICE") == "issued"
    assert map_po_status("GM APPROVED", "OPEN", "RECEIVED", "INVOICED") == "fully_received"
    assert map_po_status("GM APPROVED", "OPEN", "PLACING ORDER", "APPROVING") == "approved"
    assert map_po_status("GM APPROVED", "COMPLETED", None, None) == "closed"
    assert map_po_status("OPM APPROVING", "OPEN", None, None) == "in_review"


def test_payment_param_optional_backcompat():
    assert map_po_status("GM APPROVED", "OPEN", "DELIVERING") == "issued"
