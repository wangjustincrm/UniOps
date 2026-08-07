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
    subject = subject.replace("\r", " ").replace("\n", " ")   # a subject is a single header line
    heading = _substitute(_field(template, "heading"), esc_values)
    greeting = _substitute(_field(template, "greeting"), esc_values)
    intro = _substitute(_field(template, "intro"), esc_values)
    footer = _substitute(_field(template, "footer"), esc_values).replace("\n", "<br>")
    color = _safe_color(template.get("brand_color"))
    show_logo = bool(template.get("show_logo")) and bool(logo_data_url)

    _CELL = "padding:8px;border-bottom:1px solid #eee"

    def _amount_cell(l) -> str:
        """An ordinary line shows one figure. A line whose payment was reduced by a
        vendor credit shows all three, because the vendor is receiving less than
        their invoice and the advice is the only place that says why."""
        if not l.credit_applied:
            return (f"<td style='{_CELL};text-align:right'>"
                    f"{_money(l.amount, group.currency)}</td>")
        return (
            f"<td style='{_CELL};text-align:right'>"
            f"<div>{_money(l.gross, group.currency)}</div>"
            f"<div style='color:#666;font-size:12px'>"
            f"less credits {_money(l.credit_applied, group.currency)}</div>"
            f"<div style='font-weight:600'>{_money(l.amount, group.currency)}</div>"
            f"</td>"
        )

    rows = "".join(
        "<tr>"
        f"<td style='{_CELL}'>"
        f"{escape(l.vendor_inv_no if is_vendor else l.doc_number)}</td>"
        f"<td style='{_CELL}'>{l.payment_date}</td>"
        + _amount_cell(l) +
        "</tr>"
        for l in group.lines
    )

    # The HTML height attribute is load-bearing: Outlook renders via Word and
    # ignores CSS max-height/max-width, so a CSS-only cap lets the logo render at
    # its native pixel size. With only height set (not width) Outlook scales
    # width proportionally, so the aspect ratio is preserved. The style block
    # repeats it for clients that honour CSS.
    logo_html = (
        f'<img src="{logo_data_url}" alt="{escape(company_name)}" height="40" '
        f'style="height:40px;width:auto;margin-bottom:12px;display:block;border:0">'
        if show_logo else ""
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
