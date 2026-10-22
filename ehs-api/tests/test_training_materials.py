"""Training material: any file, links, and a session's copy of its course's.

Round 6, TC-06: "When the training session is created, if there are
attachments attached in the training course, transfer a copy over to the
session. add the ability to add a url and a video, all file types would be
good."

The file server is replaced with a stand-in that records what was stored and
what was deleted: the rule worth testing is when a shared file may go.
"""
import uuid
from datetime import date

import pytest

from app.models.training import Course
from app.services import materials
from tests.conftest import authed_client, make_token, make_user


class FakeFileServer:
    def __init__(self):
        self.stored: dict[uuid.UUID, str] = {}
        self.deleted: list[uuid.UUID] = []

    async def upload(self, data, name, ctype, doc_type, doc_id, token):
        fid = uuid.uuid4()
        self.stored[fid] = name
        return fid

    async def delete(self, file_id, token):
        self.deleted.append(file_id)


@pytest.fixture
def files(monkeypatch):
    fake = FakeFileServer()
    monkeypatch.setattr(materials, "upload_to_file_server", fake.upload)
    monkeypatch.setattr(materials, "delete_from_file_server", fake.delete)
    return fake


@pytest.fixture
async def course(db_session):
    c = Course(id=uuid.uuid4(), code=f"M-{uuid.uuid4().hex[:6]}", name="Ladder safety",
               is_statutory=False, applies_to_all=False)
    db_session.add(c)
    await db_session.flush()
    return c


def _file(name: str, ctype: str, body: bytes = b"content"):
    return {"file": (name, body, ctype)}


class TestAnyFileButARunnableOne:
    @pytest.mark.parametrize("name,ctype", [
        ("deck.pdf", "application/pdf"),
        ("walkthrough.mp4", "video/mp4"),
        ("sign-off.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("roster.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("slides.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("phone-clip.mov", "video/quicktime"),
        ("briefing.mp3", "audio/mpeg"),
    ])
    async def test_accepted(self, hse_manager, course, files, name, ctype):
        _, client = hse_manager
        got = await client.post(f"/api/v1/training/courses/{course.id}/materials",
                                files=_file(name, ctype))
        assert got.status_code == 201, got.text
        assert got.json()["filename"] == name

    @pytest.mark.parametrize("name,ctype", [
        ("page.html", "text/html"),
        ("logo.svg", "image/svg+xml"),
        ("innocent.pdf.html", "application/pdf"),
        ("setup.exe", "application/octet-stream"),
    ])
    async def test_a_page_or_a_program_is_refused(self, hse_manager, course, files, name, ctype):
        """★ The module serves what it stores; a file that can carry script is a
        page, not a handout. Judged on the name as well as the type, because
        the type is whatever the browser said."""
        _, client = hse_manager
        got = await client.post(f"/api/v1/training/courses/{course.id}/materials",
                                files=_file(name, ctype))
        assert got.status_code == 415, got.text
        assert files.stored == {}


class TestLinks:
    @pytest.mark.parametrize("name,ctype", [
        ("bundle.zip", "application/zip"),
        ("unknown.bin", "application/octet-stream"),
    ])
    async def test_what_the_file_server_would_refuse_is_refused_in_words(
            self, hse_manager, course, files, name, ctype):
        """★ Round 7, TC-05: the stand-in file server above accepts anything, so
        these tests passed while the real one refused a video with a raw 415.
        What Safety accepts is now a list the file server is known to keep for
        it, and anything else is refused here, saying what would be taken."""
        _, client = hse_manager
        got = await client.post(f"/api/v1/training/courses/{course.id}/materials",
                                files=_file(name, ctype))
        assert got.status_code == 415
        assert "PowerPoint" in got.text and "MP4" in got.text
        assert files.stored == {}

    async def test_a_link_is_an_item_named_by_its_title_or_its_site(
            self, hse_manager, course):
        _, client = hse_manager
        titled = await client.post(f"/api/v1/training/courses/{course.id}/materials/link",
                                   json={"url": "https://videos.example.com/ladders",
                                         "title": "Ladder video"})
        assert titled.status_code == 201, titled.text
        assert titled.json()["filename"] == "Ladder video"
        bare = await client.post(f"/api/v1/training/courses/{course.id}/materials/link",
                                 json={"url": "https://videos.example.com/x"})
        assert bare.json()["filename"] == "videos.example.com"
        listed = (await client.get(f"/api/v1/training/courses/{course.id}/materials")).json()
        assert {m["url"] for m in listed} == {"https://videos.example.com/ladders",
                                              "https://videos.example.com/x"}

    @pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://files.example.com/a",
                                     "videos.example.com", "data:text/html,hi"])
    async def test_only_web_addresses(self, hse_manager, course, url):
        _, client = hse_manager
        got = await client.post(f"/api/v1/training/courses/{course.id}/materials/link",
                                json={"url": url})
        assert got.status_code == 422, got.text

    async def test_a_link_has_nothing_to_download(self, hse_manager, course):
        _, client = hse_manager
        made = (await client.post(f"/api/v1/training/courses/{course.id}/materials/link",
                                  json={"url": "https://example.com/v"})).json()
        got = await client.get(f"/api/v1/training/courses/{course.id}/materials/{made['id']}")
        assert got.status_code == 409 and "https://example.com/v" in got.text


class TestASessionStartsWithACopy:
    async def _session(self, client, course, runner=None):
        body = {"course_id": str(course.id), "title": "Ladders, Tuesday",
                "session_date": date.today().isoformat()}
        if runner is not None:
            body["coordinator_id"] = str(runner.id)
        made = await client.post("/api/v1/course-sessions", json=body)
        assert made.status_code in (200, 201), made.text
        return made.json()["id"]

    async def test_the_course_s_files_and_links_are_on_a_new_session(
            self, hse_manager, course, files):
        _, client = hse_manager
        deck = (await client.post(f"/api/v1/training/courses/{course.id}/materials",
                                  files=_file("deck.pdf", "application/pdf"))).json()
        await client.post(f"/api/v1/training/courses/{course.id}/materials/link",
                          json={"url": "https://example.com/video", "title": "Video"})
        sid = await self._session(client, course)
        got = (await client.get(f"/api/v1/course-sessions/{sid}/materials")).json()
        assert [m["filename"] for m in got] == ["deck.pdf", "Video"]
        assert len(files.stored) == 1, "the same file, referenced again — not uploaded twice"
        assert deck["id"] not in {m["id"] for m in got}

    async def test_a_shared_file_goes_only_with_its_last_reference(
            self, hse_manager, course, files):
        _, client = hse_manager
        deck = (await client.post(f"/api/v1/training/courses/{course.id}/materials",
                                  files=_file("deck.pdf", "application/pdf"))).json()
        sid = await self._session(client, course)
        [copy] = (await client.get(f"/api/v1/course-sessions/{sid}/materials")).json()

        gone = await client.delete(f"/api/v1/training/courses/{course.id}/materials/{deck['id']}")
        assert gone.status_code == 204, gone.text
        assert files.deleted == [], "the session still uses it"

        gone = await client.delete(f"/api/v1/course-sessions/{sid}/materials/{copy['id']}")
        assert gone.status_code == 204, gone.text
        assert len(files.deleted) == 1

    async def test_the_runner_adds_to_their_session_and_nobody_else_does(
            self, hse_manager, worker, course, files, test_engine, db_session):
        _, hse = hse_manager
        runner_user, runner = worker
        sid = await self._session(hse, course, runner=runner_user)

        added = await runner.post(f"/api/v1/course-sessions/{sid}/materials",
                                  files=_file("handout.pdf", "application/pdf"))
        assert added.status_code == 201, added.text
        linked = await runner.post(f"/api/v1/course-sessions/{sid}/materials/link",
                                   json={"url": "https://example.com/v"})
        assert linked.status_code == 201, linked.text
        assert len((await runner.get(f"/api/v1/course-sessions/{sid}/materials")).json()) == 2

        stranger = await make_user(test_engine, role="requester")
        async with authed_client(make_token(stranger.id, stranger.role), db_session) as c:
            assert (await c.post(f"/api/v1/course-sessions/{sid}/materials/link",
                                 json={"url": "https://example.com/v"})).status_code == 403
            assert (await c.get(f"/api/v1/course-sessions/{sid}/materials")).status_code == 403


class TestFilesOnATrainingRecord:
    """★ Round 9, question 6: "allow for several files on a training record.
    all kinds of file types." """

    async def _record(self, client, course, test_engine):
        person = await make_user(test_engine, role="requester")
        made = await client.post("/api/v1/training/records", json={
            "user_id": str(person.id), "course_id": str(course.id),
            "completed_on": date.today().isoformat(), "delivery": "external"})
        assert made.status_code in (200, 201), made.text
        return made.json()["id"], person

    async def test_several_files_and_a_link(self, hse_manager, course, files, test_engine):
        _, client = hse_manager
        rid, _ = await self._record(client, course, test_engine)
        base = f"/api/v1/training/records/{rid}/materials"
        for name, ctype in (("certificate.pdf", "application/pdf"),
                            ("wallet-card.jpg", "image/jpeg"),
                            ("outline.docx",
                             "application/vnd.openxmlformats-officedocument.wordprocessingml.document")):
            got = await client.post(base, files=_file(name, ctype))
            assert got.status_code == 201, got.text
        link = await client.post(f"{base}/link", json={"url": "https://example.com/c", "title": "Online"})
        assert link.status_code == 201, link.text
        listed = (await client.get(base)).json()
        assert [m["filename"] for m in listed] == [
            "certificate.pdf", "wallet-card.jpg", "outline.docx", "Online"]

        # What can run is still refused.
        assert (await client.post(base, files=_file("x.html", "text/html"))).status_code == 415

        # Removing one leaves the others, and its file goes.
        first = listed[0]
        assert (await client.delete(f"{base}/{first['id']}")).status_code == 204
        assert len((await client.get(base)).json()) == 3
        assert len(files.deleted) == 1

    async def test_a_file_belongs_to_its_own_record(self, hse_manager, course, files, test_engine):
        _, client = hse_manager
        one, _ = await self._record(client, course, test_engine)
        two, _ = await self._record(client, course, test_engine)
        made = (await client.post(f"/api/v1/training/records/{one}/materials",
                                  files=_file("c.pdf", "application/pdf"))).json()
        assert (await client.get(f"/api/v1/training/records/{two}/materials")).json() == []
        wrong = await client.delete(f"/api/v1/training/records/{two}/materials/{made['id']}")
        assert wrong.status_code == 404

    async def test_an_employee_cannot_attach_to_a_record(self, hse_manager, worker, course, files,
                                                        test_engine):
        _, hse = hse_manager
        _, client = worker
        rid, _ = await self._record(hse, course, test_engine)
        got = await client.post(f"/api/v1/training/records/{rid}/materials",
                                files=_file("c.pdf", "application/pdf"))
        assert got.status_code == 403
        # The admission half: HSE can.
        assert (await hse.post(f"/api/v1/training/records/{rid}/materials",
                               files=_file("c.pdf", "application/pdf"))).status_code == 201
