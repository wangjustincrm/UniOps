from app.crud import engine


def test_tra_registered_with_gm_chain_and_noop_post_approve():
    assert "tra" in engine._DOC_META
    assert engine._DOC_META["tra"]["task_approve"] == "approve_tra"
    roles = [s["role"] for s in engine._WORKFLOW_DEFAULTS["tra"]]
    assert roles == ["dept_manager", "finance_manager", "gm"]
    # TRA must NOT reuse the expense reimbursement post-approve (would spawn a
    # bogus finance_bp "process reimbursement" task for a non-financial doc).
    assert engine._POST_APPROVE["tra"] is engine._post_approve_tra
    assert engine._POST_APPROVE["tra"] is not engine._post_approve_exp
