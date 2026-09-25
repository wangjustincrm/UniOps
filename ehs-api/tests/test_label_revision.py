"""A label and the data sheet it was made from.

WHMIS 2015 wants the label on the container to say what the current sheet says.
A supplier revision can change the classification, so a label approved against
the sheet it replaces is no longer the label that belongs on the drum.
"""
import uuid
from datetime import date, datetime, timezone

import pytest

from app.models.chemical import Chemical
from app.models.label import ChemicalLabel


@pytest.fixture
async def chemical(db_session) -> Chemical:
    row = Chemical(
        id=uuid.uuid4(), chemical_no=f"CHM-{uuid.uuid4().hex[:6]}",
        name="Caustic soda 50%", is_active=True,
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def _approved_label(db_session, chemical_id, client) -> ChemicalLabel:
    """A label in the state `approve()` leaves one in.

    `approved_at` is set here because the printed sheet carries it; the approve
    path always writes it, and a row without it is not a state this module
    produces. Seeded rather than approved through the endpoint so the test is
    about the revision rule and not about what makes a label approvable.
    """
    label = ChemicalLabel(
        id=uuid.uuid4(), chemical_id=chemical_id,
        product_identifier="Caustic soda 50%",
        signal_word="Danger", status="approved",
        approved_at=datetime.now(timezone.utc), approved_by_name="Marie Tremblay",
        hazard_statements=["H314"], precautionary_statements=["P280"],
        pictograms=["GHS05"],
    )
    db_session.add(label)
    await db_session.commit()
    return label


async def test_a_new_data_sheet_stops_the_old_label_printing(
        hse_manager, chemical, db_session):
    """★ The label on the drum has to match the sheet in force.

    It was left approved and printable when a revision arrived, so the hazard
    statement somebody reads before opening a container could be the one from
    the supplier's previous classification.
    """
    _, client = hse_manager
    label = await _approved_label(db_session, chemical.id, client)

    # Printable to begin with — the denial below has to mean something.
    first = await client.get(f"/api/v1/labels/{label.id}/pdf")
    assert first.status_code == 200, first.text

    filed = await client.post(f"/api/v1/chemicals/{chemical.id}/sds", json={
        "revision": "4", "revision_date": date.today().isoformat(),
    })
    assert filed.status_code in (200, 201), filed.text

    refused = await client.get(f"/api/v1/labels/{label.id}/pdf")
    assert refused.status_code == 409, refused.text
    assert "newer safety data sheet" in refused.json()["detail"]

    # And it says so on the record rather than only at the printer.
    back = (await client.get(f"/api/v1/labels/{label.id}")).json()
    assert back["status"] == "superseded"


async def test_a_draft_label_is_left_alone_by_a_revision(
        hse_manager, chemical, db_session):
    """Only labels that were approved are superseded.

    A draft is already unprintable and is presumably being written against the
    sheet that just arrived; marking it superseded would be telling somebody
    their work in progress is out of date before they finished it.
    """
    _, client = hse_manager
    draft = ChemicalLabel(
        id=uuid.uuid4(), chemical_id=chemical.id,
        product_identifier="Caustic soda 50%", status="draft",
        hazard_statements=[], precautionary_statements=[], pictograms=[],
    )
    db_session.add(draft)
    await db_session.commit()

    await client.post(f"/api/v1/chemicals/{chemical.id}/sds", json={
        "revision": "5", "revision_date": date.today().isoformat(),
    })

    back = (await client.get(f"/api/v1/labels/{draft.id}")).json()
    assert back["status"] == "draft"
