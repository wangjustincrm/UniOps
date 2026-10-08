"""User-writable values must not become live HTML in notification emails.

Before this, _render substituted {var} values verbatim and _build_email_html
only turned newlines into <br>, so anyone able to write a vendor name, a task
title or a VMS visitor name could have EPMS mail arbitrary HTML (a fake login
link) from the company's own address under the EPMS header, to approvers.

Tested at the template-function layer so every caller is covered at once.
Every "is escaped" assertion is paired with a positive one (the escaped text IS
there / the intended markup DOES survive): a bare "no <script> in the output"
is equally satisfied by a body that was never rendered at all.
"""
import uuid

from app.models.task import Task
from app.services.notification import _build_email_html, _render, _render_task_body

EVIL_LINK = '<a href="https://evil.example/login">Re-enter your password</a>'
EVIL_SCRIPT = "<script>alert(1)</script>"


def _task(**kw) -> Task:
    return Task(
        type="approve_pr", document_type="pr", document_id=uuid.uuid4(),
        document_number="PR-ESC-1", assigned_role="requester",
        title=kw.pop("title", "Approve PR-ESC-1"), **kw,
    )


def _email(template: str, variables: dict) -> str:
    return _build_email_html(_render(template, variables, escape=True))


def test_injected_link_in_a_value_is_inert_text():
    out = _email("Vendor: {vendor}", {"vendor": EVIL_LINK})
    assert "<a href=\"https://evil.example" not in out
    assert "&lt;a href=&quot;https://evil.example/login&quot;&gt;" in out


def test_injected_script_in_a_value_is_inert_text():
    out = _email("Title: {task_title}", {"task_title": EVIL_SCRIPT})
    assert "<script>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out


def test_plain_text_render_does_not_escape():
    """Subjects and Teams cards are not HTML: escaping there would show the
    recipient a literal &amp; / &#x27; in the subject line."""
    out = _render("Invoice from {vendor}", {"vendor": "Smith & O'Brien <Ltd>"}, escape=False)
    assert out == "Invoice from Smith & O'Brien <Ltd>"


def test_real_newlines_still_become_br():
    out = _email("Hi {recipient_name},\n\nPlease review.", {"recipient_name": "Ann"})
    assert "Hi Ann,<br><br>Please review." in out


def test_br_typed_by_a_user_does_not_take_effect():
    out = _email("Vendor: {vendor}", {"vendor": "Acme<br>Payment details changed"})
    assert "Acme<br>" not in out
    assert "Acme&lt;br&gt;Payment details changed" in out


def test_template_markup_survives_while_values_are_escaped():
    """The admin-written template is trusted: its own <b> and <a href> must
    still work. Escaping the rendered body wholesale would break every
    configured email; only the substituted values may be escaped."""
    tpl = 'PR <b>{pr_number}</b> from {vendor}.\n\n<a href="{link}">Open</a>'
    out = _email(tpl, {
        "pr_number": "PR-1",
        "vendor": EVIL_SCRIPT,
        "link": "https://epms.example/pr/1?a=1&b=2",
    })
    assert "PR <b>PR-1</b>" in out
    assert '<a href="https://epms.example/pr/1?a=1&amp;b=2">Open</a>' in out
    assert "&lt;script&gt;" in out and "<script>" not in out


def test_value_cannot_break_out_of_an_href_attribute():
    out = _email('<a href="{link}">Open</a>', {"link": 'x" onmouseover="alert(1)'})
    assert 'href="x&quot; onmouseover=&quot;alert(1)"' in out


# ── task.description fallback (no admin template) ───────────────────────────

def test_description_fallback_is_escaped_but_placeholders_still_render():
    """task.description is plain text built from document data (e.g. a VMS
    visitor's self-entered name), not a trusted template."""
    task = _task(description=f"Hi {{recipient_name}}, visitor {EVIL_LINK} is waiting.")
    out = _render_task_body(None, task, {"recipient_name": "Ann"})
    assert "Hi Ann, visitor" in out
    assert "<a href" not in out
    assert "&lt;a href=&quot;https://evil.example/login&quot;&gt;" in out


def test_title_fallback_is_escaped_when_no_description():
    task = _task(title=f"Check out visitor — {EVIL_SCRIPT}", description=None)
    out = _render_task_body(None, task, {})
    assert "Check out visitor — &lt;script&gt;" in out
    assert "<script>" not in out


def test_template_without_body_falls_back_to_escaped_description():
    task = _task(description=EVIL_SCRIPT)
    out = _render_task_body({"subject": "S"}, task, {})
    assert out == "&lt;script&gt;alert(1)&lt;/script&gt;"


def test_admin_template_body_is_used_verbatim_as_markup():
    task = _task(description=EVIL_SCRIPT)
    out = _render_task_body({"body": "<b>{vendor}</b>"}, task, {"vendor": "A&B"})
    assert out == "<b>A&amp;B</b>"
