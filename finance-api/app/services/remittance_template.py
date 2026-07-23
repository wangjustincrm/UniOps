"""Remittance advice HTML.

Vendor rows deliberately omit the PA number: it means nothing to the vendor
and leaks internal numbering. Employee rows show the claim number, which is
the reference an employee actually recognises.
"""
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


def _money(amount, currency: str) -> str:
    return f"{amount:,.2f} {escape(currency)}"


def render(group: PayeeGroup, *, company_name: str, reference: str,
           payment_method: str) -> tuple[str, str]:
    is_vendor = group.recipient_kind == KIND_VENDOR
    ref_header = "Invoice No" if is_vendor else "Claim No"
    subject = f"Remittance Advice — {company_name} — {reference}"

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

    intro = (
        "The following invoices have been paid." if is_vendor
        else "The following expense claims have been paid."
    )

    html = f"""
    <div style="font-family:sans-serif;max-width:640px;margin:auto;color:#222">
      <h2 style="color:#085E5E">Remittance Advice</h2>
      <p>Dear {escape(group.party_name)},</p>
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
      <p style="color:#666;font-size:13px">
        Reference: {escape(reference)}<br>
        Payment method: {escape(_METHOD_LABEL.get(payment_method, payment_method))}
      </p>
      <p style="color:#999;font-size:12px">
        This is an automated notification from {escape(company_name)}. Please do not reply
        to this message; contact your accounts payable representative with any questions.
      </p>
    </div>
    """
    return subject, html
