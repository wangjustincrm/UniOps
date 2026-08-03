from app.api.v1.expenses import _action_key, _workflow_key


def test_tra_maps_to_tra_action_and_workflow():
    assert _action_key("TRA") == "tra"
    assert _workflow_key("TRA") == "tra"
