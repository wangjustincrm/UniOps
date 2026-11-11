"""★ Round 12, ACT-05 / D12: due dates on My Actions coloured green within 10
days, yellow within 5, red within 2; the numbers are HSE's to change, and a
worker can read them."""
import pytest

from app.models.config import EhsConfig


@pytest.fixture(autouse=True)
async def _config(db_session):
    if await db_session.get(EhsConfig, 1) is None:
        db_session.add(EhsConfig(id=1))
        await db_session.flush()


async def test_a_worker_reads_the_numbers_and_hse_changes_them(worker, hse_manager):
    _, me = worker
    _, hse = hse_manager
    got = await me.get("/api/v1/actions/due-bands")
    assert got.status_code == 200, "★ My Actions is a worker's page"
    assert got.json() == {"green": 10, "yellow": 5, "red": 2}

    saved = await hse.put("/api/v1/dashboard/settings",
                          json={"due_bands": {"green": 14, "yellow": 7, "red": 3}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["due_bands"] == {"green": 14, "yellow": 7, "red": 3}
    assert (await me.get("/api/v1/actions/due-bands")).json()["red"] == 3

    assert (await me.put("/api/v1/dashboard/settings",
                         json={"due_bands": {"green": 1, "yellow": 1, "red": 1}})).status_code == 403


async def test_bands_that_do_not_nest_are_refused(hse_manager):
    _, hse = hse_manager
    for bad in ({"green": 5, "yellow": 10, "red": 2}, {"green": 10, "yellow": 5, "red": 5},
                {"green": 10, "yellow": 5, "red": -1}, {"green": "10", "yellow": 5, "red": 2},
                {"green": 10, "yellow": 5}):
        r = await hse.put("/api/v1/dashboard/settings", json={"due_bands": bad})
        assert r.status_code == 422, (bad, r.text)
    assert (await hse.get("/api/v1/actions/due-bands")).json()["green"] == 10, "nothing was saved"
