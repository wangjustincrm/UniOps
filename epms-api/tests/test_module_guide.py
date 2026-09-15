"""The module overview must not promise what the guide cannot deliver.

This layer is prose — what a module is FOR is not a fact any code states, and
the non-EPMS modules live in services epms-api cannot import. So most of it
cannot be derived. One thing can, and it is the one that would embarrass the
assistant in front of a user: the offer to explain a document kind in more
detail. Offering `expense` and then refusing it is worse than never offering.

Both directions are asserted. A module naming a kind that does not exist is a
broken promise; a kind that exists and no module claims is a part of the system
nobody can find their way to from the overview.
"""
import pytest

from app.services import doc_type_guide

GUIDE = doc_type_guide.modules()
MODULES = GUIDE["modules"]


def test_there_are_modules_and_each_is_described():
    assert MODULES
    for m in MODULES:
        assert m["key"] and m["label"], m
        assert m["covers"], f"{m['key']} does not say what it covers"
        assert m["who_uses_it"], f"{m['key']} does not say who uses it"
        assert m["typical_things_you_do"], f"{m['key']} lists nothing you can do"


def test_module_keys_are_unique():
    keys = [m["key"] for m in MODULES]
    assert len(keys) == len(set(keys))


def test_every_deeper_offer_can_be_kept():
    """The assertion this file exists for."""
    for m in MODULES:
        for kind in m["can_explain_further"]:
            assert kind in doc_type_guide.SUPPORTED, (
                f"{m['key']} offers to explain {kind!r}, which has no knowledge file")
            # Not just registered — actually loadable. A declared topic whose
            # YAML is missing would pass the check above and fail in front of
            # a user.
            assert doc_type_guide.build(kind)["types"]


def test_every_document_kind_is_reachable_from_some_module():
    """A kind nobody claims is one no reader can navigate to."""
    claimed = {k for m in MODULES for k in m["can_explain_further"]}
    assert set(doc_type_guide.SUPPORTED) - claimed == set()


def test_declared_documents_are_filtered_not_echoed():
    """can_explain_further is derived from _TOPICS, not copied from the YAML.

    Proved by construction: a kind written in the file but absent from _TOPICS
    must not survive into the payload.
    """
    raw = doc_type_guide._load_modules()
    declared = {d for m in raw["modules"] for d in (m.get("documents") or [])}
    surfaced = {k for m in MODULES for k in m["can_explain_further"]}
    assert surfaced == {d for d in declared if d in doc_type_guide.SUPPORTED}


def test_the_overview_says_what_it_is():
    assert GUIDE["what_this_is"]
    assert set(GUIDE["deeper_available_for"]) == set(doc_type_guide.SUPPORTED)


@pytest.mark.parametrize("key", ("procurement", "oa", "vms", "finance", "mrp"))
def test_the_modules_people_actually_use_are_present(key):
    """Mirrors Portal's navigation. If a module is dropped from the overview,
    someone asking "where do I do X" gets told it does not exist."""
    assert key in {m["key"] for m in MODULES}
