"""Cutover: Sales' numbers start above Zoho's and a sequence is never lowered (FR-OB-22)."""
import pytest

from app.scripts.raise_numbers import _args, run
from tests.test_orders import env  # noqa: F401 — a fixture


async def _next(env, seq):
    [r] = await env.q(f"SELECT last_value, is_called FROM {seq}")
    return r["last_value"] + 1 if r["is_called"] else r["last_value"]


async def test_a_dry_run_changes_nothing_and_apply_raises_each_sequence(env):  # noqa: F811
    before = await _next(env, "sales_order_number_seq")
    r = await run(env.factory, {"order": 1_009_000, "control": 1_009_000, "transfer": 107}, apply=False)
    assert r["order"] == {"next_before": before, "next_after": 1_009_000}
    assert await _next(env, "sales_order_number_seq") == before
    await run(env.factory, {"order": 1_009_000, "control": 1_009_000, "transfer": 107}, apply=True)
    assert [await _next(env, s) for s in ("sales_order_number_seq", "sales_x12_control_seq", "sales_transfer_number_seq")] == [
        1_009_000, 1_009_000, 107]
    assert await env.q("SELECT 1 FROM sales_log WHERE step = 'cutover'")


async def test_a_sequence_is_never_lowered(env):  # noqa: F811
    await run(env.factory, {"order": 2_000_000}, apply=True)
    r = await run(env.factory, {"order": 1_000}, apply=True)
    assert r["order"]["next_after"] == 2_000_000 and await _next(env, "sales_order_number_seq") == 2_000_000


def test_the_arguments():
    assert _args(["--order", "5", "--transfer", "7", "--apply"]) == ({"order": 5, "transfer": 7}, True)
    with pytest.raises(ValueError):
        import asyncio
        asyncio.run(run(None, {"nope": 1}, apply=False))
