/**
 * One-or-more email addresses in a single field.
 *
 * Vendor `contact_email` / `remittance_email` are free-text columns that AP
 * has always used to hold several people ("kyle@x.com; rob@x.com"). Until
 * 2026-08-17 the form rejected anything but one address, so the extra ones
 * were smuggled in through imports — and a semicolon-joined value made the
 * remittance email fail at the SMTP layer with `501 5.1.3 Bad recipient
 * address syntax`, because a semicolon is not an RFC 5322 separator and
 * Python's address parser collapses the entire malformed header into one
 * EMPTY recipient rather than salvaging the good addresses.
 *
 * So: accept the list, and normalize it to the only separator SMTP accepts.
 * `finance-api/app/crud/remittance.py::normalize_recipients` applies the same
 * rules at send time — keep the two in sync.
 */

// Same shape the Vendor form has always used for a single address; applied
// per-element here rather than to the whole field.
const ONE_ADDRESS = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/

export interface EmailListResult {
  /** False only when a non-empty value contains an unusable address. */
  valid: boolean
  /** Canonical `a@x.com, b@x.com` form; the raw trimmed input when invalid. */
  normalized: string
}

export function normalizeEmailList(raw: string): EmailListResult {
  const parts = raw
    .split(/[,;]/)
    .map((p) => p.trim())
    .filter((p) => p !== '')

  // Blank is "no email", not "bad email" — the vendor form allows it and the
  // remittance panel reports it separately as a missing address.
  if (parts.length === 0) return { valid: true, normalized: '' }

  if (!parts.every((p) => ONE_ADDRESS.test(p))) {
    return { valid: false, normalized: raw.trim() }
  }
  return { valid: true, normalized: parts.join(', ') }
}
