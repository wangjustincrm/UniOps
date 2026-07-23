# Remittance Email Template Customization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an administrator customize every part of the remittance advice email except the payment table — subject, heading, greeting, intro, footer, brand colour, and a logo toggle — from Portal → Admin → Remittance Advice, with a live preview.

**Architecture:** The customizable fields nest inside the existing `remittance_config` JSONB blob (no migration). `render()` in `remittance_template.py` gains an optional template dict + logo and does `{{placeholder}}` substitution at render time; with no template passed it produces today's email byte-for-byte. `remittance_config.load()` surfaces the template + logo, `send_groups` threads them into `render()`. The Portal settings section gains the fields, a placeholder legend, and a pure-frontend live preview.

**Tech Stack:** FastAPI + SQLAlchemy async (finance-api), aiosmtplib, React + TypeScript + Vite (Portal), pytest.

**Spec:** `docs/superpowers/specs/2026-07-23-remittance-email-template-customization-design.md`

**Worktree:** `C:/Project/uniops-remittance`, branch `feature/batch-payment-remittance`. All paths below are relative to that root.

## Global Constraints

- **No migration, no backend schema change, no epms change.** The template nests in the existing `remittance_config` JSONB; epms-api's `PATCH /config` already accepts an arbitrary dict for it.
- **Default (unconfigured) output must equal today's email.** Every template field falls back to the current hardcoded string when blank/absent; `show_logo` defaults to **false**; the brand colour defaults to `#085E5E`, which is today's heading colour.
- **Spec §4 contradiction resolved:** brand colour drives the **heading `<h2>` colour only**. The table-header background stays the existing light grey (`#f4f4f4`). Applying brand colour to the table header would change the unconfigured email (§1's "today exactly" is the stronger constraint). The plan implements heading-only; do not colour the table header.
- **Escaping is exact and non-negotiable:** template text is admin-authored (system_admin-gated) and rendered as trusted HTML (multi-line fields convert `\n`→`<br>`; subject is plain text, no `<br>`). Placeholder **values** are `html.escape`d before substitution into HTML body fields, but **not** escaped for the subject (an email header is plain text, not HTML — escaping would emit `&amp;`).
- **Brand colour is validated** against `^#[0-9a-fA-F]{3,8}$`; an invalid value falls back to `#085E5E`, because it is interpolated into a `style="color:…"` attribute.
- **finance-api pytest needs** `TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9`. Run pytest in the FOREGROUND, one session at a time; never two finance-api sessions at once (shared test DB).
- **Never run alembic or scripts from the host shell** (host `.env` points at production) — not needed here anyway, there is no migration.
- **Portal is TypeScript 6.0.3:** typecheck with `cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`; baseline is 0 errors.
- **All user-facing copy is English.**

## File Structure

| File | Change |
| --- | --- |
| `finance-api/app/services/remittance_template.py` | `render()` gains `template` + `logo_data_url`; adds `_DEFAULT_TEMPLATE`, `_substitute`, `_safe_color`; assembles logo + branded heading + customizable text around the unchanged table (Task 1) |
| `finance-api/app/services/remittance_config.py` | `RemittanceSettings` gains `template: dict` + `logo_data_url: str | None`; `load()` populates them (Task 2) |
| `finance-api/app/crud/remittance_send.py` | pass `template` + `logo_data_url` into `render()` (Task 2) |
| `finance-api/tests/test_remittance.py` | template rendering tests (Task 1), threading test (Task 2) |
| `portal/src/pages/admin/AdminPanel.tsx` | `RemittanceSettings` gains template fields, placeholder legend, live preview (Task 3) |

---

### Task 1: `render()` template substitution, brand colour, logo

**Files:**
- Modify: `finance-api/app/services/remittance_template.py`
- Test: `finance-api/tests/test_remittance.py`

**Interfaces:**
- Consumes: `crud.remittance.PayeeGroup` (has `.recipient_kind`, `.party_name`, `.currency`, `.total`, `.lines[]` with `.vendor_inv_no`, `.doc_number`, `.payment_date`, `.amount`); `models.remittance.KIND_VENDOR`.
- Produces: `services.remittance_template.render(group, *, company_name, reference, payment_method, template=None, logo_data_url=None) -> tuple[str, str]`. `template` is a dict with optional keys `subject, heading, greeting, intro, footer, brand_color, show_logo`. Also produces module constant `DEFAULT_TEMPLATE` (a dict) for reuse.

- [ ] **Step 1: Write the failing tests**

```python
# finance-api/tests/test_remittance.py — append. `_group` and `tpl` are the existing
# helpers already imported/defined in this file (tpl = app.services.remittance_template,
# _group builds a PayeeGroup). Reuse them; do not redefine.

def test_template_substitutes_placeholders_in_all_text_fields():
    g = _group()  # vendor group, party_name "ACME", one line VINV-1 / 100.00 CAD
    template = {
        "subject": "Payment to {{payee_name}} — {{reference}}",
        "heading": "{{company_name}} Remittance",
        "greeting": "Hello {{payee_name}},",
        "intro": "We paid your {{doc_type}}, total {{total}} via {{payment_method}}.",
        "footer": "Ref {{reference}} • {{currency}}",
    }
    subject, html = tpl.render(g, company_name="Canada Royal Milk",
                               reference="BP-20260723-0001", payment_method="bank_transfer",
                               template=template)
    assert subject == "Payment to ACME — BP-20260723-0001"
    assert "Canada Royal Milk Remittance" in html
    assert "Hello ACME," in html
    assert "We paid your invoices, total 100.00 CAD via Bank Transfer." in html
    assert "Ref BP-20260723-0001 • CAD" in html


def test_blank_or_absent_template_falls_back_to_defaults():
    g = _group()
    # absent
    subject1, html1 = tpl.render(g, company_name="CRM", reference="R", payment_method="eft")
    # present but all-blank
    subject2, html2 = tpl.render(g, company_name="CRM", reference="R", payment_method="eft",
                                 template={"subject": "", "heading": "  ", "intro": None})
    assert subject1 == subject2 == "Remittance Advice — CRM — R"
    assert "Remittance Advice" in html1 and "Remittance Advice" in html2
    assert "The following invoices have been paid." in html1


def test_doc_type_differs_vendor_vs_employee():
    from app.crud import remittance as rem
    vendor = _group()  # recipient_kind == 'vendor'
    employee = rem.PayeeGroup(recipient_kind="employee", party_id=vendor.party_id,
                              party_name="Jane Doe", currency="CAD",
                              lines=list(vendor.lines), total=vendor.total)
    _, hv = tpl.render(vendor, company_name="C", reference="R", payment_method="eft",
                       template={"intro": "Paid: {{doc_type}}"})
    _, he = tpl.render(employee, company_name="C", reference="R", payment_method="eft",
                       template={"intro": "Paid: {{doc_type}}"})
    assert "Paid: invoices" in hv
    assert "Paid: expense claims" in he


def test_invalid_brand_color_falls_back_and_is_not_emitted_raw():
    g = _group()
    _, html = tpl.render(g, company_name="C", reference="R", payment_method="eft",
                         template={"brand_color": "red;}</style><script>x</script>"})
    assert "<script>" not in html
    assert "#085E5E" in html            # fell back to the default heading colour


def test_placeholder_value_is_escaped_in_html_but_subject_is_plain():
    g = _group()
    g.party_name = "<b>ACME</b> & Co"
    subject, html = tpl.render(g, company_name="C", reference="R", payment_method="eft",
                               template={"greeting": "Dear {{payee_name}},",
                                         "subject": "To {{payee_name}}"})
    assert "&lt;b&gt;ACME&lt;/b&gt; &amp; Co" in html      # escaped in the HTML body
    assert "<b>ACME</b>" not in html
    assert subject == "To <b>ACME</b> & Co"                # raw in the plain-text subject


def test_logo_shown_only_when_enabled_and_present():
    g = _group()
    _, with_logo = tpl.render(g, company_name="C", reference="R", payment_method="eft",
                              template={"show_logo": True}, logo_data_url="data:image/png;base64,AAAA")
    _, no_flag = tpl.render(g, company_name="C", reference="R", payment_method="eft",
                            template={"show_logo": False}, logo_data_url="data:image/png;base64,AAAA")
    _, no_url = tpl.render(g, company_name="C", reference="R", payment_method="eft",
                           template={"show_logo": True}, logo_data_url=None)
    assert 'src="data:image/png;base64,AAAA"' in with_logo
    assert "<img" not in no_flag
    assert "<img" not in no_url


def test_no_template_arg_still_renders_todays_email():
    g = _group()
    subject, html = tpl.render(g, company_name="Canada Royal Milk",
                               reference="BP-1", payment_method="bank_transfer")
    assert subject == "Remittance Advice — Canada Royal Milk — BP-1"
    assert "The following invoices have been paid." in html
    assert "This is an automated notification" in html
```

Before writing them, open `finance-api/tests/test_remittance.py` and confirm the exact form of the existing `_group()` helper and the `tpl` import (from Task 7 of the remittance build). If `_group()` builds a vendor group with `party_name="ACME"`, `currency="CAD"`, one line `vendor_inv_no="VINV-1"`, `amount=Decimal("100.00")`, `total=Decimal("100.00")`, the assertions above hold. If its sample values differ, adjust the expected strings to match — do not change the helper.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 python -m pytest tests/test_remittance.py -k "template or brand_color or doc_type or logo_shown or todays_email" -v`
Expected: FAIL — `render()` does not accept `template` / `logo_data_url` (TypeError: unexpected keyword argument).

- [ ] **Step 3: Rewrite `remittance_template.py`**

```python
# finance-api/app/services/remittance_template.py
"""Remittance advice HTML.

The payment table is code-generated and fixed. Everything around it — subject,
heading, greeting, intro, footer, heading colour, and an optional logo — is
customizable via a `template` dict (from company_config.remittance_config.template,
resolved in services/remittance_config.py). With template=None every string falls
back to DEFAULT_TEMPLATE, which reproduces the original hardcoded email exactly.

Escaping: template text is admin-authored (system_admin-gated) and treated as
trusted HTML — multi-line fields convert \\n -> <br>; the subject is a plain-text
header. Placeholder VALUES come from data and are html.escape'd before going into
HTML body fields (a vendor named "<script>" must not inject), but NOT for the
subject header. Vendor rows still omit the PA number — it means nothing to the
vendor and leaks internal numbering.
"""
import re
from html import escape

from app.crud.remittance import PayeeGroup
from app.models.remittance import KIND_VENDOR

_METHOD_LABEL = {
    "bank_transfer": "Bank Transfer",
    "eft": "EFT",
    "cheque": "Cheque",
    "wire": "Wire",
    "other": "Other",
}

# The original hardcoded email, as templates. Each is the fallback when the
# admin leaves the corresponding field blank. Do not change these strings
# without intending to change the default email everyone gets.
DEFAULT_TEMPLATE = {
    "subject":  "Remittance Advice — {{company_name}} — {{reference}}",
    "heading":  "Remittance Advice",
    "greeting": "Dear {{payee_name}},",
    "intro":    "The following {{doc_type}} have been paid.",
    "footer":   ("Reference: {{reference}}\nPayment method: {{payment_method}}\n\n"
                 "This is an automated notification from {{company_name}}. Please do "
                 "not reply to this message; contact your accounts payable "
                 "representative with any questions."),
    "brand_color": "#085E5E",
    "show_logo": False,
}

_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}$")
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _money(amount, currency: str) -> str:
    return f"{amount:,.2f} {escape(currency)}"


def _safe_color(value) -> str:
    """A hex colour, else the default — the value goes into a style attribute."""
    if isinstance(value, str) and _COLOR_RE.fullmatch(value.strip()):
        return value.strip()
    return DEFAULT_TEMPLATE["brand_color"]


def _substitute(text: str, values: dict[str, str]) -> str:
    """Replace {{key}} with values[key]; unknown keys are left as-is (an admin
    typo shows the literal token rather than blanking silently)."""
    return _PLACEHOLDER_RE.sub(
        lambda m: values[m.group(1)] if m.group(1) in values else m.group(0), text)


def _field(template: dict, key: str) -> str:
    v = template.get(key)
    return v.strip() if isinstance(v, str) and v.strip() else DEFAULT_TEMPLATE[key]


def render(group: PayeeGroup, *, company_name: str, reference: str,
           payment_method: str, template: dict | None = None,
           logo_data_url: str | None = None) -> tuple[str, str]:
    template = template or {}
    is_vendor = group.recipient_kind == KIND_VENDOR
    ref_header = "Invoice No" if is_vendor else "Claim No"
    doc_type = "invoices" if is_vendor else "expense claims"
    method_label = _METHOD_LABEL.get(payment_method, payment_method)

    # Raw values for the plain-text subject; escaped values for HTML body fields.
    raw_values = {
        "company_name": company_name,
        "payee_name": group.party_name,
        "doc_type": doc_type,
        "reference": reference,
        "total": _money(group.total, group.currency),
        "payment_method": method_label,
        "currency": group.currency,
    }
    esc_values = {k: escape(v) for k, v in raw_values.items()}

    subject = _substitute(_field(template, "subject"), raw_values)
    heading = _substitute(_field(template, "heading"), esc_values)
    greeting = _substitute(_field(template, "greeting"), esc_values)
    intro = _substitute(_field(template, "intro"), esc_values)
    footer = _substitute(_field(template, "footer"), esc_values).replace("\n", "<br>")
    color = _safe_color(template.get("brand_color"))
    show_logo = bool(template.get("show_logo")) and bool(logo_data_url)

    rows = "".join(
        "<tr>"
        f"<td style='padding:8px;border-bottom:1px solid #eee'>"
        f"{escape(l.vendor_inv_no if is_vendor else l.doc_number)}</td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee'>{l.payment_date}</td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee;text-align:right'>"
        f"{_money(l.amount, group.currency)}</td>"
        "</tr>"
        for l in group.lines
    )

    logo_html = (
        f'<img src="{logo_data_url}" alt="{escape(company_name)}" '
        f'style="max-height:48px;margin-bottom:12px">' if show_logo else ""
    )

    html = f"""
    <div style="font-family:sans-serif;max-width:640px;margin:auto;color:#222">
      {logo_html}
      <h2 style="color:{color}">{heading}</h2>
      <p>{greeting}</p>
      <p>{intro}</p>
      <table style="width:100%;border-collapse:collapse;margin:16px 0">
        <thead>
          <tr style="background:#f4f4f4">
            <th style="padding:8px;text-align:left">{ref_header}</th>
            <th style="padding:8px;text-align:left">Payment Date</th>
            <th style="padding:8px;text-align:right">Amount</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
        <tfoot>
          <tr>
            <td colspan="2" style="padding:8px;font-weight:bold;text-align:right">Total</td>
            <td style="padding:8px;font-weight:bold;text-align:right">
              {_money(group.total, group.currency)}</td>
          </tr>
        </tfoot>
      </table>
      <div style="color:#666;font-size:13px">{footer}</div>
    </div>
    """
    return subject, html
```

Note the two intentional differences from the pre-existing file, both required by the design and both preserving the default output's content: (1) the old email had two separate footer paragraphs (a `#666`/13px "Reference / Payment method" block and a `#999`/12px disclaimer); they are now one customizable `footer` field rendered in a single `#666`/13px block — same words, unified shade. (2) the heading colour is now `{color}` (defaults to `#085E5E`, unchanged). The table markup and the `Invoice No`/`Claim No` header logic are byte-identical to before.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 python -m pytest tests/test_remittance.py -v`
Expected: PASS, including the pre-existing Task 7 template tests (they call `render()` without the new args, which still works).

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/services/remittance_template.py finance-api/tests/test_remittance.py
git commit -m "feat(finance): customizable remittance email template with placeholder substitution"
```

---

### Task 2: Thread the template and logo from config to render

**Files:**
- Modify: `finance-api/app/services/remittance_config.py`
- Modify: `finance-api/app/crud/remittance_send.py`
- Test: `finance-api/tests/test_remittance.py`

**Interfaces:**
- Consumes: `render(..., template=, logo_data_url=)` from Task 1; `RemittanceSettings`.
- Produces: `RemittanceSettings` with two new fields `template: dict` and `logo_data_url: str | None`; `send_groups` passes them into `render`.

- [ ] **Step 1: Write the failing tests**

```python
# finance-api/tests/test_remittance.py — append. Reuse the file's existing helpers:
# `_configured` (seeds an enabled remittance_config + po_smtp on company_config),
# `_vendor`, `_invoice`, `_pa`, `_record`, `rc` (services.remittance_config),
# `rsend` (crud.remittance_send), the `SENT`/scope constants, and `_group`.

async def test_load_surfaces_template_and_logo(db_session):
    import sqlalchemy as sa
    db_session.add(CompanyConfig(role_management={}, remittance_config={
        "enabled": True, "from_email": "ap@crm.test",
        "template": {"heading": "Custom Heading", "show_logo": True}}))
    await db_session.flush()
    await db_session.execute(sa.text(
        "UPDATE company_config SET po_smtp_host='po.host', po_smtp_port=587,"
        " po_smtp_use_tls=true, logo_data_url='data:image/png;base64,ZZZ'"))

    s = await rc.load(db_session)
    assert s is not None
    assert s.template.get("heading") == "Custom Heading"
    assert s.template.get("show_logo") is True
    assert s.logo_data_url == "data:image/png;base64,ZZZ"


async def test_send_uses_the_configured_template(db_session):
    # A configured heading must reach the actually-sent email.
    await _configured(db_session)  # enables remittance + po_smtp
    import sqlalchemy as sa
    await db_session.execute(sa.text(
        "UPDATE company_config SET remittance_config = remittance_config || "
        "'{\"template\": {\"heading\": \"CRM Payment Notice\"}}'::jsonb"))
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-77")
    pa = _pa(bp.id, "12.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    sent_html = {}
    from unittest.mock import AsyncMock, patch
    async def _capture(to, subject, html, **kw):
        sent_html["subject"], sent_html["html"] = subject, html
    with patch("app.crud.remittance_send.send_email", new=AsyncMock(side_effect=_capture)):
        sender = await rc.load(db_session)
        await rsend.send_groups(
            db_session, scope_kind=SCOPE_PAYMENT, scope_id=rec.id,
            groups=await rem.build_groups(db_session, [rec]),
            reference="PAY-1", payment_method="bank_transfer",
            company_name="CRM", sender=sender, actor_id=uuid.uuid4())
    assert "CRM Payment Notice" in sent_html["html"]
```

Confirm the real names of `_configured`, `_vendor`, `_invoice`, `_pa`, `_record`, `SCOPE_PAYMENT`, and `rem` against the current `test_remittance.py` before relying on them — this file was built across Tasks 1–10 of the remittance feature and these helpers exist, but match their exact signatures.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 python -m pytest tests/test_remittance.py -k "surfaces_template or uses_the_configured_template" -v`
Expected: FAIL — `RemittanceSettings` has no `template` / `logo_data_url` attribute; the sent HTML does not contain the custom heading (render is called without a template).

- [ ] **Step 3: Add the fields to `RemittanceSettings` and populate them in `load()`**

```python
# finance-api/app/services/remittance_config.py

# add to the @dataclass(frozen=True) class RemittanceSettings, after smtp_use_tls:
    template: dict = field(default_factory=dict)
    logo_data_url: str | None = None
```

Add `from dataclasses import dataclass, field` (the file currently imports only `dataclass`). Then in `load()`:

- Add `logo_data_url` to the SELECT column list:
  `"SELECT remittance_config, logo_data_url, po_smtp_host, ..."`.
- In the returned `RemittanceSettings(...)`, add:
  `template=cfg.get("template") or {},`
  `logo_data_url=(row["logo_data_url"] or None),`

The `field(default_factory=dict)` default keeps the existing test helper `_sender()` (which constructs `RemittanceSettings(...)` without these two args) working unchanged.

- [ ] **Step 4: Pass them into `render()` in `send_groups`**

In `finance-api/app/crud/remittance_send.py`, the `render(...)` call (currently `render(g, company_name=company_name, reference=reference, payment_method=payment_method)`) becomes:

```python
            subject, html = render(g, company_name=company_name, reference=reference,
                                    payment_method=payment_method,
                                    template=sender.template,
                                    logo_data_url=sender.logo_data_url)
```

`sender` is the `RemittanceSettings` already passed into `send_groups`; no signature change to `send_groups` is needed.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 python -m pytest tests/test_remittance.py -v`
Expected: PASS (all remittance tests, including Task 1's and the pre-existing suite).

- [ ] **Step 6: Commit**

```bash
git add finance-api/app/services/remittance_config.py finance-api/app/crud/remittance_send.py finance-api/tests/test_remittance.py
git commit -m "feat(finance): thread remittance template and logo from config into the sent email"
```

---

### Task 3: Portal settings — template fields, legend, live preview

**Files:**
- Modify: `portal/src/pages/admin/AdminPanel.tsx` (the `RemittanceSettings` component and the `CompanyConfig['remittance_config']` type)

**Interfaces:**
- Consumes: the `remittance_config.template` shape from Tasks 1–2 (`subject, heading, greeting, intro, footer, brand_color, show_logo`).
- Produces: no new exports; extends the existing section.

- [ ] **Step 1: Extend the `remittance_config` type**

In `portal/src/pages/admin/AdminPanel.tsx`, the `CompanyConfig` interface's `remittance_config` field currently lists `enabled, from_email, from_name, cc_email, smtp_user, smtp_password`. Add a `template` sub-object:

```ts
  remittance_config?: {
    enabled?: boolean
    from_email?: string
    from_name?: string
    cc_email?: string
    smtp_user?: string
    smtp_password?: string
    template?: {
      subject?: string
      heading?: string
      greeting?: string
      intro?: string
      footer?: string
      brand_color?: string
      show_logo?: boolean
    }
  } | null
```

- [ ] **Step 2: Add template state + fields to `RemittanceSettings`**

In the `RemittanceSettings` component, alongside the existing sender-identity state, add local state for each template field, seeded from the loaded config the same way the existing fields are (`state ?? rc.template?.field ?? default`). Add these controls under the SMTP credential-override block, inside a titled sub-section "Email Template":

- **Subject** — single-line `Input`, default placeholder `Remittance Advice — {{company_name}} — {{reference}}`
- **Heading** — single-line `Input`
- **Greeting** — single-line `Input`
- **Intro** — multi-line `Textarea`
- **Footer** — multi-line `Textarea`
- **Brand Colour** — `<input type="color">` (native), plus a small text showing the hex; default `#085E5E`
- **Show company logo** — `Toggle`

Use the file's existing `Field`, `Input`, `Textarea`, `Toggle` helpers. The default values shown when a field is blank must match the backend `DEFAULT_TEMPLATE` strings verbatim (copy them from `finance-api/app/services/remittance_template.py`) so the editor previews what will actually send.

Include a **placeholder legend** — a short muted line under the fields:

```tsx
<p className="text-xs text-neutral-500">
  Available placeholders: {'{{company_name}}'}, {'{{payee_name}}'}, {'{{doc_type}}'}, {'{{reference}}'}, {'{{total}}'}, {'{{payment_method}}'}, {'{{currency}}'}
</p>
```

On save, fold the template fields into the existing `save.mutateAsync({ remittance_config: {...} })` call by adding a `template` key built the same way the sender fields are — always include `show_logo` (a boolean the admin set) and `brand_color`; include the text fields only when non-blank (mirrors the backend's blank→default fallback, and avoids writing `""` for an untouched field). Keep the existing `isLoading` save-guard.

- [ ] **Step 3: Add the live preview**

Add a right-hand (or below-the-fields) preview box that renders the non-table parts using a small local substitution helper and sample values. Pure frontend — no backend call:

```tsx
const SAMPLE: Record<string, string> = {
  company_name: 'Canada Royal Milk',
  payee_name: 'Acme Supplies Ltd',
  doc_type: 'invoices',
  reference: 'BP-20260723-0001',
  total: '1,234.56 CAD',
  payment_method: 'Bank Transfer',
  currency: 'CAD',
}
const fill = (t: string) => t.replace(/\{\{(\w+)\}\}/g, (_, k) => SAMPLE[k] ?? `{{${k}}}`)
```

Render, in a bordered box: the logo (`cfg.logo_data_url` `<img>` capped at 48px) only when Show-logo is on and a logo exists; the heading via `fill(headingVal)` in the chosen brand colour; the greeting; the intro; a muted `[ Payment table appears here ]` block; then the footer via `fill(footerVal)` with `\n`→`<br>` (use `white-space: pre-line` or split on `\n`). Label it "Preview (sample data — the payment table is added automatically)". The preview is illustrative, not a byte-exact mirror of the backend HTML.

Note `cfg.logo_data_url` already exists on the Portal `CompanyConfig` type (used by Company Settings) — read it from the loaded config; do not add an upload here.

- [ ] **Step 4: Typecheck**

Run: `cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: 0 errors (baseline).

- [ ] **Step 5: Verify against the running dev stack**

The dev stack serves this branch (Portal on http://localhost:5174). Open Admin → Remittance Advice. Confirm: the Email Template fields appear; editing the Heading/Intro/Footer/colour updates the preview live; toggling Show-logo shows/hides the company logo in the preview; Save persists (reload the section and the values remain). No send is needed for this task.

- [ ] **Step 6: Commit**

```bash
git add portal/src/pages/admin/AdminPanel.tsx
git commit -m "feat(portal-ui): remittance email template editor with placeholders and live preview"
```

---

## Self-Review

**Spec coverage:**
- §1 data model (template in remittance_config, no migration) → Task 2 (load reads it) + Task 3 (Portal writes it). ✓
- §2 placeholders (backend substitution, the 7 tokens, unknown left as-is) → Task 1 `_substitute` + tests. ✓
- §3 escaping/safety (trusted template, escaped values, subject plain, colour validated, logo conditional) → Task 1 (`_safe_color`, raw vs esc values, `show_logo and logo_data_url`) + tests. ✓
- §4 assembly order + meta folded into footer → Task 1 render body. ✓ (heading-only colour resolution documented in Global Constraints)
- §5 backend code (render params, load surfaces template+logo, send threads them) → Tasks 1–2. ✓
- §6 Portal fields + legend + preview → Task 3. ✓
- §7 testing → Tasks 1–2 tests; Portal typecheck Task 3. ✓

**Placeholder scan:** no TBD/TODO; every code step shows complete code or an exact, bounded edit against a named anchor. ✓

**Type consistency:** `render(group, *, company_name, reference, payment_method, template=None, logo_data_url=None)` used identically in Task 1 (def), Task 2 (call). `RemittanceSettings.template` / `.logo_data_url` defined in Task 2 Step 3, consumed in Task 2 Step 4. `DEFAULT_TEMPLATE` keys (`subject/heading/greeting/intro/footer/brand_color/show_logo`) match the Portal type in Task 3 and the placeholder set in Task 1. ✓
