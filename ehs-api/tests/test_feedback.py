"""General comments, and the escaping every Safety email now depends on."""
import pytest

from app.services import mail


class TestLeavingAComment:
    """Round 4: "add a general comments feedback place to allow for general
    comments for other issues found."
    """

    async def test_an_ordinary_employee_can_leave_one_and_read_their_own(
            self, worker, db_session):  # noqa: ARG002
        _, client = worker
        left = await client.post("/api/v1/feedback", json={
            "body": "The training list does not show people who have left, and "
                    "the audit needs their records.",
            "page_path": "/training",
        })
        assert left.status_code in (200, 201), left.text
        assert left.json()["status"] == "new"
        assert left.json()["submitted_by_name"]

        mine = (await client.get("/api/v1/feedback")).json()
        assert [c["id"] for c in mine] == [left.json()["id"]]

    async def test_an_employee_does_not_read_somebody_elses(
            self, worker, hse_manager):
        """★ Asking for everything must not hand over everything.

        A comment about somebody's own work is not a register to browse. The
        filter is forced rather than the page refused, because refusing would
        also hide a person's own comments from them.
        """
        _, hse = hse_manager
        theirs = await hse.post("/api/v1/feedback", json={"body": "HSE's own note"})
        assert theirs.status_code in (200, 201), theirs.text

        _, client = worker
        ours = await client.post("/api/v1/feedback", json={"body": "A worker's note"})
        assert ours.status_code in (200, 201), ours.text

        listed = (await client.get("/api/v1/feedback?mine=false")).json()
        ids = [c["id"] for c in listed]
        assert ours.json()["id"] in ids, "their own must be readable"
        assert theirs.json()["id"] not in ids

        # And HSE sees both, which is what makes the denial above mean
        # something rather than "nobody can read anything".
        all_of_them = [c["id"] for c in (await hse.get("/api/v1/feedback")).json()]
        assert ours.json()["id"] in all_of_them
        assert theirs.json()["id"] in all_of_them

    async def test_only_hse_answers_one(self, worker, hse_manager):
        _, client = worker
        left = (await client.post("/api/v1/feedback", json={
            "body": "The eyewash form asks for a serial number twice."})).json()

        refused = await client.patch(f"/api/v1/feedback/{left['id']}",
                                     json={"status": "done", "reply": "Fixed."})
        assert refused.status_code == 403, refused.text

        _, hse = hse_manager
        answered = await hse.patch(f"/api/v1/feedback/{left['id']}",
                                   json={"status": "done", "reply": "Fixed on Friday."})
        assert answered.status_code == 200, answered.text
        body = answered.json()
        assert body["status"] == "done"
        assert body["reply"] == "Fixed on Friday."
        assert body["answered_by_name"]
        # ★ The comment itself is never rewritten.
        assert body["body"] == "The eyewash form asks for a serial number twice."

    async def test_an_empty_comment_is_refused(self, worker):
        _, client = worker
        blank = await client.post("/api/v1/feedback", json={"body": "   "})
        # Either the schema refuses it or the strip leaves nothing; both are
        # fine, a blank row in a list of complaints is not.
        if blank.status_code in (200, 201):
            pytest.fail("a comment with nothing in it was accepted")
        assert blank.status_code == 422


class TestEmailsDoNotCarryWhatSomebodyTyped:
    """★★ Every Safety email interpolates text a person typed.

    Raised in review by uniops-9a. Unescaped, somebody could write a link into
    a free-text field and have this module send it from the plant's own address
    under the plant's own heading — a phishing mail composed through a text box.
    Eleven callers pass user text into `render`; this is tested at the shell so
    all of them are covered.
    """

    def test_a_link_typed_into_a_field_is_not_a_link_in_the_mail(self):
        out = mail.render(
            '<a href="https://elsewhere/login">Your session expired</a>',
            title="Safety feedback")
        assert "<a href=\"https://elsewhere/login\"" not in out
        assert "&lt;a href=" in out
        # The words survive — escaping must not eat the message.
        assert "Your session expired" in out

    def test_a_script_tag_is_text(self):
        out = mail.render("<script>alert(1)</script>", title="t")
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_the_title_is_escaped_too(self):
        out = mail.render("body", title='Report from <b>"Bobby"</b>')
        assert "<b>" not in out
        assert "&lt;b&gt;" in out

    def test_line_breaks_still_become_breaks(self):
        out = mail.render("one\ntwo", title="t")
        assert "one<br>two" in out

    def test_a_break_somebody_typed_does_not(self):
        out = mail.render("one<br>two", title="t")
        assert "one&lt;br&gt;two" in out

    def test_the_button_survives_escaping(self):
        out = mail.render("body", title="t", link="https://safety.example/feedback",
                          link_label="Open feedback")
        assert 'href="https://safety.example/feedback"' in out
        assert "Open feedback" in out
