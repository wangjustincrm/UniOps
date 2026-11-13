"""Card data out of stored pages (user 2026-11-04): the keys go, the orders stay,
the hash follows, a later pull of the same page finds it, and nothing is written
on a dry run, twice, or to the log beyond counts and key names."""
import hashlib
import json


from app.integrations.shopify import page_bytes, strip_card_data
from app.models.documents import Document
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.redact_card_documents import card_keys, find, quiet_sql, run
from tests.test_orders import env  # noqa: F401 — a fixture

SECRET = "424242"
CARD = {"credit_card_bin": SECRET, "credit_card_number": "x 4242", "credit_card_name": "A B", "credit_card_wallet": None,
        "credit_card_company": "Visa", "credit_card_expiration_month": 1, "credit_card_expiration_year": 2030,
        "avs_result_code": "Y", "cvv_result_code": "M"}
TX = {"id": 1, "amount": "10.00", "gateway": "shopify_payments", "payment_details": CARD, "receipt": {"payment_id": "x"}}
ORDERS = [{"id": 11, "name": "#1", "gift_card": False, "refunds": [{"id": 5, "transactions": [TX]}]}, {"id": 12, "name": "#2", "refunds": []}]


async def add(t, content: bytes, *, kind="SHOPIFY_ORDER_JSON", sha=None, body=None) -> int:
    async with t.factory(SYSTEM_ACTOR) as u:
        d = Document(direction="IN", origin="PARTNER", system="SHOPIFY", kind=kind, size=len(content), content=content,
                     sha256=sha or hashlib.sha256(content).hexdigest(), processing_status="Processed", body_json=body)
        u.session.add(d)
        await u.session.flush()
        await u.commit()
        return d.id


async def stored(t, i):
    [r] = await t.q(f"SELECT content, sha256, size FROM sales_documents WHERE id = {i}")
    return r


def raw(orders=ORDERS) -> bytes:
    return json.dumps({"orders": orders}).encode()  # not the stored form on purpose: the old pages may differ


async def test_it_removes_every_listed_key_and_leaves_the_orders_with_the_new_hash(env):  # noqa: F811
    i = await add(env, raw())
    out = await run(env.factory, apply=True)
    assert out["by_status"] == {"redacted": 1}
    [d] = out["documents"]
    assert (d["orders_before"], d["orders_after"]) == (2, 2)
    assert d["keys"]["payment_details"] == 1 and d["keys"]["receipt"] == 1 and d["keys"]["credit_card_bin"] == 1
    assert d["keys"]["avs_result_code"] == 1 and d["keys"]["cvv_result_code"] == 1 and d["keys_removed"] == 11
    r = await stored(env, i)
    data = bytes(r["content"])
    assert card_keys(json.loads(data)) == {}
    for banned in (SECRET, b"credit_card", b"payment_details", b"receipt", b"avs_result", b"cvv_result"):
        assert (banned.encode() if isinstance(banned, str) else banned) not in data
    page = json.loads(data)
    assert [o["id"] for o in page["orders"]] == [11, 12] and page["orders"][0]["gift_card"] is False  # gift_card stays
    assert page["orders"][0]["refunds"][0]["transactions"][0]["amount"] == "10.00"
    assert r["sha256"] == hashlib.sha256(data).hexdigest() and r["size"] == len(data)


async def test_the_redacted_bytes_are_what_a_later_pull_of_that_page_stores(env):  # noqa: F811
    i = await add(env, raw())
    await run(env.factory, apply=True)
    fresh = page_bytes({"orders": strip_card_data(ORDERS)})  # shopify_intake._store_page: found by its hash, not stored twice
    assert bytes((await stored(env, i))["content"]) == fresh


async def test_a_dry_run_changes_nothing(env):  # noqa: F811
    i = await add(env, raw())
    before = await stored(env, i)
    out = await run(env.factory, apply=False)
    assert out["by_status"] == {"would_redact": 1} and out["documents"][0]["orders_after"] == 2
    assert await stored(env, i) == before
    assert await env.q("SELECT 1 FROM sales_log WHERE step = 'redaction'") == []


async def test_it_is_idempotent_and_writes_one_log_line_without_a_value(env):  # noqa: F811
    i = await add(env, raw())
    await run(env.factory, apply=True)
    after = await stored(env, i)
    assert await find(env.factory) == []
    assert (await run(env.factory, apply=True))["found"] == 0
    assert await stored(env, i) == after
    [log] = await env.q("SELECT message, context, document_id FROM sales_log WHERE step = 'redaction'")
    assert log["document_id"] == i and log["context"]["keys_removed"] == 11 and "payment_details" in log["context"]["key_names"]
    assert SECRET not in json.dumps(log, default=str)


async def test_nothing_of_the_old_content_reaches_the_audit(env):  # noqa: F811
    await add(env, raw())
    await run(env.factory, apply=True)
    assert await env.q(f"SELECT 1 FROM sales_audit WHERE before::text LIKE '%{SECRET}%' OR after::text LIKE '%{SECRET}%'") == []


async def test_the_parsed_body_is_redacted_too(env):  # noqa: F811
    i = await add(env, raw(), body={"orders": ORDERS})
    await run(env.factory, apply=True)
    [r] = await env.q(f"SELECT body_json FROM sales_documents WHERE id = {i}")
    assert card_keys(r["body_json"]) == {}


async def test_a_clean_page_and_another_kind_of_file_are_not_touched(env):  # noqa: F811
    clean = await add(env, page_bytes({"orders": strip_card_data(ORDERS)}))
    edi = await add(env, b'ISA*"receipt"*not json~', kind="945_EDI")
    before = await stored(env, clean), await stored(env, edi)
    out = await run(env.factory, apply=True)
    assert out["documents"] == []  # only Shopify pages are looked at (card data came only there): the 945 is not read
    assert (await stored(env, clean), await stored(env, edi)) == before


async def test_a_hash_another_document_holds_is_reported_and_left(env):  # noqa: F811
    await add(env, page_bytes({"orders": strip_card_data(ORDERS)}))
    i = await add(env, raw())
    before = await stored(env, i)
    out = await run(env.factory, apply=True)
    assert out["by_status"] == {"hash_taken": 1}
    assert await stored(env, i) == before


def test_quiet_sql_turns_the_echo_off_and_hides_parameters():
    from app.db.session import engine
    engine.sync_engine.echo = True
    quiet_sql()
    assert engine.sync_engine.echo is False and engine.sync_engine.hide_parameters is True
