"""Once (user 2026-11-04): remove card data from stored documents. Pages stored
before the gateway stripped it (2026-11-03) kept what Shopify sent in a refund's
transaction. What is removed is `strip_card_data`: there is one definition.

    python -m app.scripts.redact_card_documents            # dry run: what it would do, nothing written
    python -m app.scripts.redact_card_documents --apply    # redact, one transaction per document

Documents are found by what they contain. Each gets its redacted bytes (exactly
what a fresh pull of that page stores), its new sha256 and size, and one
sales_log line. No copy of the old content is kept anywhere, and nothing here
prints or logs a value: counts and key names only. A document whose redacted
hash another document already holds is left and reported (uq_sales_documents_sha256).
Idempotent: a redacted document is no longer found.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

import sqlalchemy as sa

from app.integrations.shopify import CARD_KEYS, is_card_key, page_bytes, strip_card_data
from app.models.documents import Document
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.base import ScriptArgs, quiet_sql, script_main  # noqa: F401 — quiet_sql: tests import it here
from app.services import oplog

STEP = "redaction"
# What marks a document to look at; the JSON itself decides what goes.
MARKS = ('"credit_card', *(f'"{k}"' for k in sorted(CARD_KEYS)))


def card_keys(value: Any) -> Counter:
    """The card keys in this JSON, at any depth, by name."""
    found: Counter = Counter()
    if isinstance(value, dict):
        for k, v in value.items():
            if is_card_key(k):
                found[k] += 1
            found.update(card_keys(v))
    elif isinstance(value, list):
        for v in value:
            found.update(card_keys(v))
    return found


def _orders(value: Any) -> int | None:
    return len(value["orders"]) if isinstance(value, dict) and isinstance(value.get("orders"), list) else None


async def find(uow_factory) -> list[int]:
    """The documents whose bytes or parsed body carry a mark of card data."""
    content = sa.func.convert_from(Document.content, "LATIN1")  # bytes as text, for the search only
    body = sa.cast(Document.body_json, sa.Text)
    marked = sa.or_(*(content.contains(m, autoescape=True) for m in MARKS), *(body.contains(m, autoescape=True) for m in MARKS))
    async with uow_factory(SYSTEM_ACTOR) as u:
        # Card data only ever came in Shopify's pages; other kinds hold bytes that are not text (PDFs, attachments).
        return list((await u.session.execute(sa.select(Document.id).where(Document.kind == "SHOPIFY_ORDER_JSON", marked)
                                             .order_by(Document.id))).scalars())


async def redact_one(uow_factory, doc_id: int, *, apply: bool) -> dict[str, Any]:
    """One document, in one transaction."""
    async with uow_factory(SYSTEM_ACTOR) as u:
        s = u.session
        row = (await s.execute(sa.select(Document.kind, Document.content, Document.body_json).where(Document.id == doc_id)
                               .with_for_update())).one()
        out: dict[str, Any] = {"id": doc_id, "kind": row.kind}
        try:
            page = json.loads(row.content) if row.content is not None else None
        except ValueError:
            return out | {"status": "not_json"}  # a mark in something else (a file, a note): for a person to look at
        keys = card_keys(page) + card_keys(row.body_json)
        out |= {"keys": dict(sorted(keys.items())), "keys_removed": sum(keys.values()), "orders_before": _orders(page)}
        if not keys:
            return out | {"status": "no_card_keys"}  # the word, not a key
        values: dict[str, Any] = {}
        if page is not None:
            clean = strip_card_data(page)
            data = page_bytes(clean)
            sha = hashlib.sha256(data).hexdigest()
            out["orders_after"] = _orders(clean)
            taken = (await s.execute(sa.select(Document.id).where(Document.sha256 == sha, Document.id != doc_id,
                                                                  Document.content.is_not(None)))).first()
            if taken is not None:
                return out | {"status": "hash_taken", "taken_by": taken.id}
            values |= {"content": data, "sha256": sha, "size": len(data)}
        if row.body_json is not None:
            values["body_json"] = strip_card_data(row.body_json)
        if not apply:
            return out | {"status": "would_redact"}
        # A plain UPDATE: no ORM history, so no before / after copy of the old bytes anywhere.
        await s.execute(sa.update(Document.__table__).where(Document.__table__.c.id == doc_id).values(**values))
        await oplog.write(s, "INFO", STEP, f"card data removed from document {doc_id}: {out['keys_removed']} key(s)",
                          document_id=doc_id, context={"keys_removed": out["keys_removed"], "key_names": sorted(keys)})
        await u.commit()
        return out | {"status": "redacted"}


async def run(uow_factory, *, apply: bool) -> dict[str, Any]:
    docs = [await redact_one(uow_factory, i, apply=apply) for i in await find(uow_factory)]
    return {"apply": apply, "found": len(docs), "by_status": dict(Counter(d["status"] for d in docs)), "documents": docs}


async def _main(args: ScriptArgs) -> dict[str, Any]:
    from app.core.deps import uow_factory
    out = await run(uow_factory, apply=args.apply)
    out.pop("apply")
    return out


if __name__ == "__main__":
    script_main(_main)
