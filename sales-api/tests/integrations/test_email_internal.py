"""Internal mail reaches only one plain mailbox in a company domain (review 2026-11-10): an address
that merely ends in the domain ("x@evil.com#@company") is not internal."""
import pytest

from app.integrations.email import is_internal, plain_address

D = frozenset({"crm.example"})


def test_company_addresses_are_internal_with_or_without_a_name():
    assert is_internal(["a@crm.example", "Ann <b@CRM.example>"], D)
    assert plain_address("a.b+c@crm.example") == "a.b+c@crm.example"


@pytest.mark.parametrize("bad", ["x@evil.com#@crm.example", '"x@evil.com"@crm.example', "a@crm.example@evil.com",
                                 "a@evil.com", "", "a", "@crm.example", "a@crm.example>"])
def test_anything_else_is_not(bad):
    assert not is_internal([bad], D)


def test_one_outside_address_makes_the_whole_message_external():
    assert not is_internal(["a@crm.example", "b@partner.example"], D)
    assert not is_internal([], D) and not is_internal(["a@crm.example"], frozenset())
