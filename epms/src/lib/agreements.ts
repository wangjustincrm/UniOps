import type { ApiAgreement } from '@/services/agreement'

// DELIBERATELY LOOSER than the backend rule, and only safe because of that.
//
// The authority is epms-api/app/crud/agreement.py::_admissible_predicate, which
// requires status in (active, expired) AND today <= valid_to + grace_days. This
// copy skips the date test for 'active' — it cannot be made to agree, because
// its inputs differ: the backend compares Postgres-server dates, this compares
// the browser's local clock against a UTC-parsed valid_to. Any attempt at parity
// would drift by a day per timezone and start HIDING admissibility-gated UI on
// agreements the backend would happily accept.
//
// So this is a permissive-only display gate: it may say yes when the backend
// will refuse (the user gets a 422/no-op explaining why), and must never say no
// when the backend would allow. Every write route this gates independently
// re-checks via agr_crud.is_admissible server-side, which is the enforcing
// copy. Do not "fix" the divergence by tightening this — tighten it and you
// silently strand legitimate spend at a timezone boundary.
//
// Single shared copy (Task 10 fix round 1) — this used to be duplicated
// verbatim in AgreementDetailPage.tsx (Create PA / receipt-entry gates) and
// ReceiptCreatePage.tsx (agreement picker). Two copies of a business rule,
// even if identical today, only ever drift; the grace-period arithmetic isn't
// a boolean simple enough to justify re-typing it per call site.
export function isAgreementAdmissible(agreement: ApiAgreement): boolean {
  if (agreement.status === 'active') return true
  if (agreement.status !== 'expired') return false
  const daysSinceExpiry = Math.floor(
    (Date.now() - new Date(agreement.valid_to).getTime()) / 86_400_000
  )
  return daysSinceExpiry <= agreement.grace_days
}
