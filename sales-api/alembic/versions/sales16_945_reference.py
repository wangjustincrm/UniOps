"""sales16_945_reference — the 945's N9*E1 on the shipment (user 2026-11-03).

`sales_shipments.our_reference`: the header N9*E1 of the 945 set — the
reference the 940 was sent with (Zoho's 940 name during the shadow run, ours
after). It was parsed but not stored. Filled for the shipments already
received from their stored 945 (the original bytes are kept, §13.1): the set
at `set_index` (1-based), its N9*E1 before the first LX.

The backfill reads the X12 itself (the element separator is ISA's 4th
character, the segment terminator its 106th) so this migration never depends
on the parser as it changes later.

Revision id "sales16_945_reference" is 21 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa

revision = 'sales16_945_reference'
down_revision = 'sales15_order_ref'
branch_labels = None
depends_on = None


def _e1_by_set(data: bytes) -> dict[int, str]:
    """{set index (1-based): header N9*E1} for each ST*945 set of a file."""
    text = data.decode("ascii", "replace").lstrip("﻿ \r\n\t")
    if not text.startswith("ISA") or len(text) < 106:
        return {}
    el, seg = text[3], text[105]
    out: dict[int, str] = {}
    n, in_set, header = 0, False, False
    for raw in text.split(seg):
        s = raw.strip("\r\n ").split(el)
        if s[0] == "ST":
            in_set = len(s) > 1 and s[1] == "945"
            if in_set:
                n += 1
                header = True
        elif not in_set:
            continue
        elif s[0] == "LX":
            header = False
        elif s[0] == "N9" and header and len(s) > 2 and s[1].upper() == "E1" and s[2] and n not in out:
            out[n] = s[2][:80]
    return out


def upgrade() -> None:
    op.add_column('sales_shipments', sa.Column('our_reference', sa.String(80), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT s.id, s.set_index, s.document_id, d.content FROM sales_shipments s "
        "JOIN sales_documents d ON d.id = s.document_id WHERE d.content IS NOT NULL ORDER BY s.document_id")).all()
    parsed: dict[int, dict[int, str]] = {}
    for sid, set_index, doc_id, content in rows:
        if doc_id not in parsed:
            parsed[doc_id] = _e1_by_set(bytes(content))
        ref = parsed[doc_id].get(set_index)
        if ref:
            conn.execute(sa.text("UPDATE sales_shipments SET our_reference = :r WHERE id = :i"), {"r": ref, "i": sid})


def downgrade() -> None:
    op.drop_column('sales_shipments', 'our_reference')
