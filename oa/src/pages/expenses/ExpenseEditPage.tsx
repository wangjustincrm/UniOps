import { useParams } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { ErrorBanner } from '@/components/ui/ErrorBanner'
import { EDITABLE_STATUSES, useEditableClaim } from '@/lib/editableClaim'
import ExpenseCreatePage from '@/pages/expenses/ExpenseCreatePage'
import MilCreatePage from '@/pages/expenses/MilCreatePage'
import TrvCreatePage from '@/pages/expenses/TrvCreatePage'
import CfmCreatePage from '@/pages/expenses/CfmCreatePage'
import TraCreatePage from '@/pages/travel/TraCreatePage'

/**
 * Edit a claim that has been returned (or is still a draft).
 *
 * There was no such page. ExpenseDetailPage rendered an Edit button for a
 * returned claim and navigated to /expenses/edit/:id, which matched no route,
 * so the tab showed the shell's "No route matched" banner — and Return was a
 * dead end for every claim type in the module: the person who raised it could
 * not change anything and resubmit, only start again.
 *
 * Each claim type has its own form with its own rules (budget accounts, meal
 * per-diems, mileage rates, traveller rosters), so rather than write a sixth
 * form this dispatches to the one that already knows them, in edit mode. The
 * claim is fetched under the same query key those pages use, so this costs one
 * request between them, not two.
 */
export default function ExpenseEditPage() {
  const { id } = useParams<{ id: string }>()
  const { claim, isLoading, error } = useEditableClaim(id)

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-6 w-6 animate-spin text-neutral-400" />
      </div>
    )
  }
  if (error) return <ErrorBanner message={error.message} />
  if (!claim) return <ErrorBanner message="Claim not found." />

  // The server refuses a PATCH outside these statuses (crud/expense.py::
  // update_claim). Say so here rather than letting someone retype a whole
  // claim and meet a 409 at the end of it.
  if (!EDITABLE_STATUSES.includes(claim.status)) {
    return (
      <div className="p-8 text-sm text-neutral-500">
        {claim.claim_number} is {claim.status.replace('_', ' ')} and can no longer be edited.
        Only draft and returned documents can be changed.
      </div>
    )
  }

  const type = claim.claim_type.toUpperCase()
  if (type === 'EXP') return <ExpenseCreatePage editClaimId={id} />
  if (type === 'MIL') return <MilCreatePage editClaimId={id} />
  if (type === 'TRV') return <TrvCreatePage editClaimId={id} />
  if (type === 'TRA') return <TraCreatePage editClaimId={id} />
  if (type.startsWith('CFM')) return <CfmCreatePage editClaimId={id} />

  return <ErrorBanner message={`No edit form for claim type "${claim.claim_type}".`} />
}
