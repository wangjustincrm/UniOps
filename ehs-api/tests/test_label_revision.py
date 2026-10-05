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


async def test_a_chinese_product_name_does_not_break_the_printer(
        hse_manager, db_session):
    """★ The same latin-1 header that broke the certificate breaks this.

    `Content-Disposition` is built from the product name, and a header cannot
    carry one written in Chinese — the label rendered and the response then
    answered 500 on the way out, for exactly the products whose names need the
    care.
    """
    from app.models.chemical import Chemical

    _, client = hse_manager
    chem = Chemical(id=uuid.uuid4(), chemical_no=f"CHM-{uuid.uuid4().hex[:6]}",
                    name="烧碱 50%", is_active=True)
    db_session.add(chem)
    await db_session.commit()

    label = ChemicalLabel(
        id=uuid.uuid4(), chemical_id=chem.id,
        product_identifier="烧碱 50%", signal_word="Danger", status="approved",
        approved_at=datetime.now(timezone.utc), approved_by_name="Marie Tremblay",
        hazard_statements=["H314"], precautionary_statements=["P280"],
        pictograms=["GHS05"],
    )
    db_session.add(label)
    await db_session.commit()

    got = await client.get(f"/api/v1/labels/{label.id}/pdf")
    assert got.status_code == 200, got.text
    disposition = got.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition, disposition
    # And the plain filename is still something every client can read.
    assert disposition.encode("latin-1"), "the header itself has to be encodable"


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


class TestTheLabelFitsWhatIsOnIt:
    """★ Round 5, TC-21: "need a means to resize the label as some of the data
    is being cut off and PPE required freeform text is not being copied to the
    label."

    Both were real, and the first was the serious one: the layout had caps
    written into it — six hazard statements, two lines each, five pictograms —
    so a product with seven statements printed six and said nothing. A workplace
    label short of a hazard statement is a WHMIS problem, not a cosmetic one.
    """

    def _label(self, **kw):
        """A label object good enough to render, without a database."""
        from datetime import date as _date
        from types import SimpleNamespace

        base = dict(
            product_identifier="Caustic soda 50%",
            supplier="Acme Chemicals, 1 Industrial Way",
            signal_word="Danger",
            hazard_statements=[{"code": "H314", "text": "Causes severe skin burns and eye damage."}],
            precautionary_statements=[{"code": "P280", "text": "Wear protective gloves."}],
            pictograms=["GHS05"],
            first_aid="Rinse with water for 15 minutes and call for help.",
            ppe_note="Face shield, chemical apron and elbow-length gloves.",
            approved_at=_date(2026, 10, 5),
            approved_by_name="Marie Tremblay",
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_the_ppe_note_reaches_the_label(self):
        """★ It was stored on the label and drawn nowhere — the crud has copied
        it off the chemical since the first release."""
        from app.services import label_pdf

        pdf, used = label_pdf.render(self._label(), chemical_id="abc", copies=1)
        assert pdf.startswith(b"%PDF")
        assert used.code in [sz.code for sz in label_pdf.SIZES]
        text = _pdf_text(pdf)
        assert "PPE REQUIRED" in text
        assert "Face shield" in text

    def test_first_aid_is_still_there(self):
        from app.services import label_pdf

        text = _pdf_text(label_pdf.render(
            self._label(), chemical_id="abc", copies=1)[0])
        assert "FIRST AID" in text
        assert "Rinse with water" in text

    def test_every_hazard_statement_is_printed(self):
        """★ No cap. Seven statements, seven on the label."""
        from app.services import label_pdf

        many = [{"code": f"H3{i:02d}", "text": f"Hazard number {i} spelled out"}
                for i in range(1, 8)]
        text = _pdf_text(label_pdf.render(
            self._label(hazard_statements=many, first_aid=None, ppe_note=None),
            chemical_id="abc", copies=1, size="sheet")[0])
        for i in range(1, 8):
            assert f"Hazard number {i}" in text, f"statement {i} was dropped"

    def test_the_sizes_are_offered_and_differ(self):
        from app.services import label_pdf

        codes = [s.code for s in label_pdf.SIZES]
        assert codes == ["bottle", "drum", "sheet"]
        # The default is to fit rather than to pick one.
        assert label_pdf.DEFAULT_SIZE == label_pdf.FIT
        assert label_pdf.CHOICES[0] == label_pdf.FIT
        # Four small labels to a sheet, one large one.
        assert label_pdf.BY_SIZE["bottle"].per_page == 4
        assert label_pdf.BY_SIZE["sheet"].per_page == 1

    def test_too_much_for_a_bottle_is_refused_by_name(self):
        """★ The point of the change. It used to cut it off silently."""
        import pytest as _pytest

        from app.services import label_pdf

        many = [{"code": f"H3{i:02d}",
                 "text": f"Hazard number {i}, spelled out at the length these actually run to"}
                for i in range(1, 15)]
        with _pytest.raises(label_pdf.LabelTooLarge) as caught:
            label_pdf.render(self._label(hazard_statements=many),
                             chemical_id="abc", copies=1, size="bottle")
        said = str(caught.value)
        assert "does not fit" in said
        assert "14 hazard statements" in said, said
        assert "mm more" in said, "it must say how much does not fit"

    def test_the_same_content_fits_the_large_label(self):
        """The admission beside the refusal: the answer is a bigger label, and
        it works."""
        from app.services import label_pdf

        many = [{"code": f"H3{i:02d}",
                 "text": f"Hazard number {i}, spelled out at the length these actually run to"}
                for i in range(1, 15)]
        pdf, used = label_pdf.render(self._label(hazard_statements=many),
                                     chemical_id="abc", copies=1, size="sheet")
        assert pdf.startswith(b"%PDF")
        assert used.code == "sheet"
        text = _pdf_text(pdf)
        assert "Hazard number 14" in text


    def test_fit_picks_the_smallest_that_holds_it(self):
        """★ The default. A real label on the dev database — six hazard
        statements and eight precautionary ones — does not fit a half-A4 sheet at
        type you can read at arm's length. Before this round it printed anyway,
        six of each, silently. Refusing instead would be right and would also
        make the Print button fail out of the box.
        """
        from app.services import label_pdf

        # Little on it: the small stock is enough.
        _, small = label_pdf.render(
            self._label(hazard_statements=[{"code": "H315", "text": "Irritates skin."}],
                        precautionary_statements=[], first_aid=None, ppe_note=None,
                        supplier=None, pictograms=[]),
            chemical_id="abc", copies=1, size=label_pdf.FIT)
        assert small.code == "bottle"

        # The real one: it steps up rather than refusing or truncating.
        many_h = [{"code": f"H3{i:02d}", "text": f"Hazard {i} spelled out as these run"}
                  for i in range(6)]
        many_p = [{"code": f"P2{i:02d}", "text": f"Precaution {i} spelled out as these run"}
                  for i in range(8)]
        pdf, used = label_pdf.render(
            self._label(hazard_statements=many_h, precautionary_statements=many_p),
            chemical_id="abc", copies=1, size=label_pdf.FIT)
        assert used.code in ("drum", "sheet")
        text = _pdf_text(pdf)
        assert "Hazard 5" in text and "Precaution 7" in text, (
            "fitting must not mean dropping the tail of the list")


def _pdf_text(pdf: bytes) -> str:
    """The text drawn on a reportlab page.

    reportlab compresses its content streams with ASCII85 then Flate, so the
    strings are not greppable in the raw bytes — asserting on those would pass
    for the wrong reason.
    """
    import base64
    import re
    import zlib

    out = []
    for m in re.finditer(rb"\bstream\b", pdf):
        rest = pdf[m.end():].lstrip(b"\r\n")
        stop = rest.find(b"endstream")
        if stop == -1:
            continue
        raw = rest[:stop].strip()
        body = None
        for decode in (lambda d: zlib.decompress(base64.a85decode(d, adobe=True)),
                       zlib.decompress):
            try:
                body = decode(raw)
                break
            except Exception:  # noqa: BLE001 — not every stream is text
                continue
        if body is None:
            continue
        for chunk in re.findall(rb"\((.*?)\)\s*Tj", body):
            out.append(chunk.decode("latin-1"))
    # ★ An empty result would make every "is in" assertion fail and every
    # "is not in" assertion pass — so say so rather than returning "".
    assert out, "no text could be extracted from the PDF; the helper is broken"
    return "\n".join(out)
