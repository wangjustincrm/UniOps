"""Round 12, CHM-01..05: who sees a workplace label, the preview before
approval, and the emergency health contact at the top of it."""
import base64
import re
import uuid
import zlib
from datetime import datetime, timezone

from reportlab.pdfgen import canvas as pdfcanvas

from app.models.label import ChemicalLabel
from app.services import label_pdf


def _text(pdf: bytes) -> bytes:
    """Every content stream decoded (reportlab writes ASCII85 over Flate) —
    the words drawn on the page."""
    out = b""
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        raw = m.group(1).strip()
        try:
            if raw.endswith(b"~>"):
                raw = base64.a85decode(raw[:-2])
            out += zlib.decompress(raw)
        except (ValueError, zlib.error):
            out += raw
    return out


async def _chemical(client, **extra) -> dict:
    made = await client.post("/api/v1/chemicals", json={
        "name": "Caustic soda 50%", "manufacturer": "Acme Chemicals",
        "manufacturer_contact": "1-800-555-0100", **extra})
    assert made.status_code in (200, 201), made.text
    return made.json()


async def _approved(db_session, chemical_id) -> ChemicalLabel:
    label = ChemicalLabel(
        id=uuid.uuid4(), chemical_id=chemical_id, product_identifier="Caustic soda 50%",
        signal_word="Danger", status="approved", approved_at=datetime.now(timezone.utc),
        approved_by_name="Marie Tremblay",
        hazard_statements=[{"code": "H314", "text": "Causes severe skin burns."}])
    db_session.add(label)
    await db_session.commit()
    return label


async def test_the_chemical_form_carries_the_emergency_contact_and_hazards_to_a_draft(
        hse_manager):
    """CHM-03/04: entered once on the product, a new label starts with them."""
    _, client = hse_manager
    chem = await _chemical(client, emergency_contact="CANUTEC 1-888-226-8832",
                           hazard_statements=[{"code": "H314", "text": "Causes severe skin burns."}])
    assert chem["emergency_contact"] == "CANUTEC 1-888-226-8832"
    assert chem["hazard_statements"][0]["code"] == "H314"

    draft = (await client.post(f"/api/v1/chemicals/{chem['id']}/labels")).json()
    assert draft["emergency_contact"] == "CANUTEC 1-888-226-8832"
    assert [h["code"] for h in draft["hazard_statements"]] == ["H314"]

    fixed = await client.patch(f"/api/v1/labels/{draft['id']}",
                               json={"emergency_contact": "Poison centre 1-844-764-7669"})
    assert fixed.json()["emergency_contact"] == "Poison centre 1-844-764-7669"

    # And a correction on the product reaches the form it came from.
    patched = await client.patch(f"/api/v1/chemicals/{chem['id']}",
                                 json={"emergency_contact": None, "hazard_statements": []})
    assert patched.json()["emergency_contact"] is None
    assert patched.json()["hazard_statements"] == []


async def test_anyone_who_reads_chemicals_can_view_and_print_an_approved_label(
        hse_manager, worker, db_session):
    """CHM-01: the server always allowed it; viewing is not counted as a print."""
    _, hse = hse_manager
    _, client = worker
    chem = await _chemical(hse)
    label = await _approved(db_session, uuid.UUID(chem["id"]))

    viewed = await client.get(f"/api/v1/labels/{label.id}/pdf?preview=true")
    assert viewed.status_code == 200, viewed.text
    assert viewed.content.startswith(b"%PDF")
    assert b"DRAFT" not in _text(viewed.content)
    assert (await client.get(f"/api/v1/labels/{label.id}")).json()["print_count"] == 0

    printed = await client.get(f"/api/v1/labels/{label.id}/pdf")
    assert printed.status_code == 200, printed.text
    back = (await client.get(f"/api/v1/labels/{label.id}")).json()
    assert back["print_count"] == 1 and back["status"] == "printed"


async def test_a_draft_can_be_previewed_stamped_draft_but_never_printed(
        hse_manager, worker):
    """CHM-02: see the layout before approving; the gate itself is untouched."""
    _, hse = hse_manager
    _, other = worker
    chem = await _chemical(hse)
    draft = (await hse.post(f"/api/v1/chemicals/{chem['id']}/labels")).json()

    preview = await hse.get(f"/api/v1/labels/{draft['id']}/pdf?preview=true")
    assert preview.status_code == 200, preview.text
    assert b"(DRAFT)" in _text(preview.content)
    assert "draft-label" in preview.headers["content-disposition"]
    back = (await hse.get(f"/api/v1/labels/{draft['id']}")).json()
    assert back["status"] == "draft" and back["print_count"] == 0

    # Printing it for real is still refused.
    assert (await hse.get(f"/api/v1/labels/{draft['id']}/pdf")).status_code == 409
    # And somebody who cannot edit the draft cannot see it either.
    assert (await other.get(
        f"/api/v1/labels/{draft['id']}/pdf?preview=true")).status_code == 403


async def test_a_new_version_is_drafted_beside_the_approved_label(hse_manager, db_session):
    """CHM-04: the 14 labels already printed carry the contact only once a new
    version is approved; until then the old one goes on printing."""
    _, client = hse_manager
    chem = await _chemical(client, emergency_contact="CANUTEC 1-888-226-8832")
    old = await _approved(db_session, uuid.UUID(chem["id"]))
    assert old.emergency_contact is None  # locked: nothing rewrote it

    new = await client.post(f"/api/v1/chemicals/{chem['id']}/labels")
    assert new.status_code == 201, new.text
    assert new.json()["emergency_contact"] == "CANUTEC 1-888-226-8832"
    # One draft at a time.
    again = await client.post(f"/api/v1/chemicals/{chem['id']}/labels")
    assert again.status_code == 409

    # The old one still prints while the new one is a draft.
    assert (await client.get(f"/api/v1/labels/{old.id}/pdf")).status_code == 200

    await client.patch(f"/api/v1/labels/{new.json()['id']}", json={
        "hazard_statements": [{"code": "H314", "text": "Causes severe skin burns."}]})
    approved = await client.post(f"/api/v1/labels/{new.json()['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert (await client.get(f"/api/v1/labels/{old.id}")).json()["status"] == "superseded"


class _Recorder(pdfcanvas.Canvas):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.lines: list[tuple[str, float]] = []

    def drawString(self, x, y, text, *a, **k):  # noqa: N802
        self.lines.append((text, y))
        return super().drawString(x, y, text, *a, **k)


def test_manufacturer_and_emergency_contact_sit_under_the_signal_word():
    """CHM-05: top of the label, under the hazard warning, above the hazard
    statements (CHM-03: "just under the manufacturer health contact info")."""
    import io

    label = ChemicalLabel(
        id=uuid.uuid4(), chemical_id=uuid.uuid4(), product_identifier="Caustic soda 50%",
        supplier="Acme Chemicals", supplier_contact="1-800-555-0100",
        emergency_contact="CANUTEC 1-888-226-8832", signal_word="Danger",
        hazard_statements=[{"code": "H314", "text": "Causes severe skin burns."}],
        precautionary_statements=[], pictograms=[], first_aid="Rinse with water.",
        ppe_note="Gloves.", approved_at=datetime.now(timezone.utc))
    size = label_pdf.SIZES[1]
    c = _Recorder(io.BytesIO())
    label_pdf._one_label(c, label, str(label.chemical_id), 800, None,
                         size=size, scale=size.scale)
    y = {text: at for text, at in c.lines}

    assert y["DANGER"] > y["MANUFACTURER"] > y["EMERGENCY HEALTH CONTACT"]
    assert y["EMERGENCY HEALTH CONTACT"] > y["CANUTEC 1-888-226-8832"]
    assert y["CANUTEC 1-888-226-8832"] > y["Causes severe skin burns."]
    assert y["Causes severe skin burns."] > y["FIRST AID"]


def test_every_label_size_still_holds_the_new_top_block():
    """SF-13: the small stock prints at its fixed type even with two more
    lines at the top — it leaves statements for the data sheet, not the
    contact."""
    label = ChemicalLabel(
        id=uuid.uuid4(), chemical_id=uuid.uuid4(), product_identifier="Caustic soda 50%",
        supplier="Acme Chemicals", supplier_contact="1-800-555-0100",
        emergency_contact="CANUTEC 1-888-226-8832", signal_word="Danger",
        hazard_statements=[{"code": f"H{300 + i}", "text": "Causes severe skin burns and eye damage."}
                           for i in range(6)],
        precautionary_statements=[{"code": "P280", "text": "Wear protective gloves."}] * 6,
        pictograms=[], approved_at=datetime.now(timezone.utc))
    for s in label_pdf.SIZES:
        try:
            pdf, used, _ = label_pdf.render_with_omitted(
                label, chemical_id=str(label.chemical_id), size=s.code,
                sds_available=True)
        except label_pdf.LabelTooLarge:
            assert not s.always_prints, s.code
            continue
        assert pdf.startswith(b"%PDF") and used.code == s.code


async def test_a_replaced_label_says_it_was_replaced_not_that_a_sheet_came(hse_manager, db_session):
    """Review C8: the refusal names why the label stopped printing."""
    _, client = hse_manager
    chem = await _chemical(client)
    old = await _approved(db_session, uuid.UUID(chem["id"]))
    new = (await client.post(f"/api/v1/chemicals/{chem['id']}/labels")).json()
    await client.patch(f"/api/v1/labels/{new['id']}", json={
        "hazard_statements": [{"code": "H314", "text": "Causes severe skin burns."}]})
    assert (await client.post(f"/api/v1/labels/{new['id']}/approve")).status_code == 200
    refused = await client.get(f"/api/v1/labels/{old.id}/pdf")
    assert refused.status_code == 409
    assert "newer version of this label" in refused.json()["detail"]


async def test_renaming_while_a_new_version_is_drafted_leaves_one_draft(hse_manager, db_session):
    """Review C9: the approved label stops printing, the draft takes the name,
    and there is still one draft; a printed label is withdrawn too."""
    _, client = hse_manager
    chem = await _chemical(client)
    old = await _approved(db_session, uuid.UUID(chem["id"]))
    old.status = "printed"
    await db_session.commit()
    draft = (await client.post(f"/api/v1/chemicals/{chem['id']}/labels")).json()

    renamed = await client.patch(f"/api/v1/chemicals/{chem['id']}", json={"name": "Caustic soda 25%"})
    assert renamed.status_code == 200, renamed.text
    labels = (await client.get(f"/api/v1/labels?chemical_id={chem['id']}")).json()
    by_id = {x["id"]: x for x in labels}
    assert [x["status"] for x in labels].count("draft") == 1
    assert by_id[str(old.id)]["status"] == "superseded"
    assert by_id[draft["id"]]["product_identifier"] == "Caustic soda 25%"
    refused = await client.get(f"/api/v1/labels/{old.id}/pdf")
    assert refused.status_code == 409 and "renamed" in refused.json()["detail"]


async def test_renaming_withdraws_a_printed_label_with_no_draft(hse_manager, db_session):
    _, client = hse_manager
    chem = await _chemical(client)
    old = await _approved(db_session, uuid.UUID(chem["id"]))
    old.status = "printed"
    await db_session.commit()
    await client.patch(f"/api/v1/chemicals/{chem['id']}", json={"name": "Caustic soda 25%"})
    back = (await client.get(f"/api/v1/labels/{old.id}")).json()
    assert back["status"] == "draft" and back["product_identifier"] == "Caustic soda 25%"
    assert (await client.get(f"/api/v1/labels/{old.id}/pdf")).status_code == 409
