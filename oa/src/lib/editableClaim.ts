// Shared plumbing for "edit a claim that was sent back".
//
// Until now OA had no edit route at all. The detail page rendered an Edit
// button for a `returned` claim and navigated to /expenses/edit/:id — a path
// that matched nothing, so the tab showed the shell's "No route matched"
// banner. That made Return a dead end for the whole module: an approver could
// send a claim back, and the person who raised it had no way to change
// anything and resubmit. Only a brand-new claim could get past it.
//
// Rather than write five more forms, each Create page takes an optional
// `editClaimId` and reuses everything it already has — the budget-account
// picker, tax codes, receipt scanning, the meal-limit warnings. This module is
// what they share: how to load the claim, and how to save it to the right verb.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'

export interface EditableLineItem {
  line_number: number
  expense_date: string
  description: string
  budget_account_id: string
  budget_account_code: string
  budget_account_name: string
  cost_center_id: string | null
  cost_center_name: string | null
  total_amount: string
  tax_amount: string
  net_amount: string
  tax_code: string | null
}

export interface EditableTripItem {
  trip_number: number
  trip_date: string
  from_location: string
  to_location: string
  purpose: string
  is_round_trip: boolean
  distance_km: string
  rate_per_km: string
  amount: string
  budget_account_id: string | null
  budget_account_code: string | null
  budget_account_name: string | null
}

export interface EditableTraveler {
  user_id: string
  user_name: string
  seq: number
}

export interface EditableClaim {
  id: string
  claim_number: string
  claim_type: string
  status: string
  submission_date: string
  currency: string
  notes: string | null
  purpose: string | null
  vehicle_description: string | null
  vehicle_owned_by: string | null
  travel_from_date: string | null
  travel_to_date: string | null
  travel_destination: string | null
  transport_modes: string[]
  leave_from_date: string | null
  leave_to_date: string | null
  travel_application_id: string | null
  line_items: EditableLineItem[]
  trip_items: EditableTripItem[]
  travelers: EditableTraveler[]
}

/** The prop every Create page grows to double as its own Edit page. */
export interface EditableFormProps {
  editClaimId?: string
}

/** Statuses the server will accept a PATCH for (crud/expense.py::update_claim). */
export const EDITABLE_STATUSES = ['draft', 'returned']

export function useEditableClaim(claimId?: string) {
  const { data, isLoading, error } = useQuery<EditableClaim>({
    // Same key the detail page uses, so opening Edit from a claim already on
    // screen is instant and the two views cannot disagree.
    queryKey: ['expense', claimId],
    queryFn: () => api.get<EditableClaim>(`/api/v1/expenses/${claimId}`),
    enabled: !!claimId,
  })
  return {
    claim: data,
    // Only "loading" when there is something to load — a plain create page must
    // not sit behind a spinner waiting for a request it never made.
    isLoading: !!claimId && isLoading,
    error: error as Error | undefined,
  }
}

/**
 * POST a new claim or PATCH the one being edited, then land on its detail page.
 *
 * PATCH deliberately does not send `claim_type`: the type is fixed at creation
 * and ExpenseClaimUpdate has no field for it.
 */
export function useSaveClaim(editClaimId: string | undefined, onSaved: (id: string) => void) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => {
      if (editClaimId) {
        const { claim_type: _ignored, ...rest } = body
        return api.patch<{ id: string }>(`/api/v1/expenses/${editClaimId}`, rest)
      }
      return api.post<{ id: string }>('/api/v1/expenses', body)
    },
    onSuccess: (data) => {
      if (editClaimId) {
        // The detail page, its permissions, and the task lists all describe a
        // claim that just changed underneath them.
        qc.invalidateQueries({ queryKey: ['expense', editClaimId] })
        qc.invalidateQueries({ queryKey: ['expense-permissions', editClaimId] })
        qc.invalidateQueries({ queryKey: ['oa-tasks'] })
        qc.invalidateQueries({ queryKey: ['expenses'] })
      }
      onSaved(data.id ?? editClaimId ?? '')
    },
  })
}
