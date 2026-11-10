"""ISS-0211: Custom Forms admin settings (OA.md §6.1) — Description and
Default Currency are saved, and the list says which workflow a form really
uses: cfm_<code> when Portal → Approval Workflows defines it, else `cfm`
(approval-api engine._get_workflow's fallback)."""
import uuid

import pytest
from sqlalchemy import select

import app.db.base as db_module
from app.models.company_config_mirror import EpmsCompanyConfig
from tests.conftest import _client, _make_token


async def _merge_workflow_defs(defs: dict) -> None:
    async with db_module.AsyncSessionLocal() as db:
        cfg = (await db.execute(select(EpmsCompanyConfig))).scalars().first()
        if cfg is None:
            cfg = EpmsCompanyConfig(id=uuid.uuid4(), dept_gm_opm_mapping={},
                                    role_management={}, workflow_defs={})
            db.add(cfg)
        merged = {**(cfg.workflow_defs or {}), **defs}
        cfg.workflow_defs = {k: v for k, v in merged.items() if v is not None}
        await db.commit()


@pytest.mark.asyncio
async def test_settings_round_trip_and_effective_workflow(test_engine):
    own, shared = f"W{uuid.uuid4().hex[:4].upper()}", f"V{uuid.uuid4().hex[:4].upper()}"
    async with _client(_make_token("system_admin")) as c:
        r = await c.post("/api/v1/expenses/custom-forms", json={
            "code": own, "name": "Own chain", "description": "Shown to staff",
            "default_currency": "USD", "fields": []})
        assert r.status_code == 201, r.text
        r = await c.post("/api/v1/expenses/custom-forms", json={
            "code": shared, "name": "Default chain", "fields": []})
        assert r.status_code == 201, r.text
        r = await c.patch(f"/api/v1/expenses/custom-forms/{shared}",
                          json={"description": "Edited", "default_currency": "USD"})
        assert r.status_code == 200, r.text

        await _merge_workflow_defs({f"cfm_{own.lower()}": [{"role": "gm_or_opm", "label": "GM"}]})
        try:
            forms = {f["code"]: f for f in (await c.get("/api/v1/expenses/custom-forms")).json()}
        finally:
            await _merge_workflow_defs({f"cfm_{own.lower()}": None})

    assert forms[own]["description"] == "Shown to staff"
    assert forms[own]["default_currency"] == "USD"
    assert forms[own]["effective_workflow_key"] == f"cfm_{own.lower()}"
    assert forms[shared]["description"] == "Edited"
    assert forms[shared]["default_currency"] == "USD"
    assert forms[shared]["effective_workflow_key"] == "cfm"
