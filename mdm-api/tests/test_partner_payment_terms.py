"""Partner payment terms are validated on write, not on read."""
import pytest
from pydantic import ValidationError

from app.schemas.business_partner import PartnerCreate, PartnerOut, PartnerUpdate


def test_write_accepts_custom_net_days():
    assert PartnerCreate(code="X", name="X", payment_terms="Net 21").payment_terms == "net21"
    assert PartnerUpdate(payment_terms="net14").payment_terms == "net14"
    assert PartnerUpdate().payment_terms is None


@pytest.mark.parametrize("raw", ["prepaid", "net0", "net366", "monthly"])
def test_write_rejects_unreadable_terms(raw):
    with pytest.raises(ValidationError):
        PartnerCreate(code="X", name="X", payment_terms=raw)
    with pytest.raises(ValidationError):
        PartnerUpdate(payment_terms=raw)


def test_read_still_serialises_legacy_values():
    # A row an older writer left behind must not 500 the partner list.
    out = PartnerOut.model_validate({
        "id": "00000000-0000-0000-0000-000000000001",
        "code": "X", "name": "X", "payment_terms": "prepaid",
    })
    assert out.payment_terms == "prepaid"
