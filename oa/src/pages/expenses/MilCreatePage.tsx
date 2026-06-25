import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Plus, Trash2, AlertTriangle } from 'lucide-react'
import { formatAmount } from '@/lib/utils'
import { api } from '@/lib/api'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Policy {
  mileage_rate_per_km: number
  mileage_budget_account_id: string | null
  max_km_per_claim: number
}

interface TripItem {
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

// ── Helpers ───────────────────────────────────────────────────────────────────

function today() {
  return new Date().toISOString().slice(0, 10)
}

function calcAmount(km: string, rate: string, roundTrip: boolean): string {
  const k = parseFloat(km), r = parseFloat(rate)
  if (isNaN(k) || isNaN(r)) return '0.00'
  return ((roundTrip ? 2 : 1) * k * r).toFixed(2)
}

function emptyTrip(n: number, rate: string, accountId: string | null, code: string | null, name: string | null): TripItem {
  return {
    trip_number: n,
    trip_date: today(),
    from_location: '',
    to_location: '',
    purpose: '',
    is_round_trip: false,
    distance_km: '',
    rate_per_km: rate,
    amount: '0.00',
    budget_account_id: accountId,
    budget_account_code: code,
    budget_account_name: name,
  }
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function MilCreatePage() {
  const navigate = useNavigate()

  const { data: policy } = useQuery<Policy>({
    queryKey: ['expense-policy'],
    queryFn: () => api.get<Policy>('/api/v1/policy'),
  })

  const rate = policy?.mileage_rate_per_km?.toString() ?? '0.72'
  const maxKm = policy?.max_km_per_claim ?? 2000
  const defaultAccountId = policy?.mileage_budget_account_id ?? null

  const [submissionDate, setSubmissionDate] = useState(today())
  const [notes, setNotes] = useState('')
  const [vehicleDesc, setVehicleDesc] = useState('')
  const [vehicleOwnedBy, setVehicleOwnedBy] = useState('self')
  const [trips, setTrips] = useState<TripItem[]>([
    emptyTrip(1, rate, defaultAccountId, null, null),
  ])

  // Sync rate when policy loads
  const currentRate = rate

  const updateTrip = (idx: number, patch: Partial<TripItem>) => {
    setTrips((prev) => {
      const next = [...prev]
      const updated = { ...next[idx], ...patch }
      // Recalculate amount if km or rate changed
      if ('distance_km' in patch || 'is_round_trip' in patch) {
        updated.amount = calcAmount(
          'distance_km' in patch ? (patch.distance_km ?? updated.distance_km) : updated.distance_km,
          updated.rate_per_km,
          'is_round_trip' in patch ? (patch.is_round_trip ?? updated.is_round_trip) : updated.is_round_trip,
        )
      }
      next[idx] = updated
      return next
    })
  }

  const totalKm = trips.reduce((s, t) => {
    const km = parseFloat(t.distance_km) || 0
    return s + (t.is_round_trip ? km * 2 : km)
  }, 0)
  const totalAmount = trips.reduce((s, t) => s + (parseFloat(t.amount) || 0), 0)
  const isOverMaxKm = totalKm > maxKm

  const createMutation = useMutation({
    mutationFn: (body: object) => api.post('/api/v1/expenses', body),
    onSuccess: (data: any) => navigate(`/expenses/${data.id}`),
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const validTrips = trips.filter((t) => t.from_location && t.to_location && t.distance_km)
    if (validTrips.length === 0) return alert('Add at least one trip')
    if (!vehicleDesc.trim()) return alert('Vehicle description is required')

    createMutation.mutate({
      claim_type: 'MIL',
      submission_date: submissionDate,
      currency: 'CAD',
      notes: notes || null,
      purpose: 'Mileage reimbursement',
      vehicle_description: vehicleDesc,
      vehicle_owned_by: vehicleOwnedBy,
      line_items: [],
      trip_items: validTrips.map((t) => ({
        trip_number: t.trip_number,
        trip_date: t.trip_date,
        from_location: t.from_location,
        to_location: t.to_location,
        purpose: t.purpose,
        is_round_trip: t.is_round_trip,
        distance_km: parseFloat(t.distance_km).toFixed(2),
        rate_per_km: parseFloat(t.rate_per_km).toFixed(4),
        amount: parseFloat(t.amount).toFixed(2),
        budget_account_id: t.budget_account_id,
        budget_account_code: t.budget_account_code,
        budget_account_name: t.budget_account_name,
      })),
    })
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6 max-w-5xl">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">New Mileage Claim</h1>
          <p className="mt-0.5 text-sm text-neutral-500">Personal vehicle reimbursement at ${currentRate}/km</p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => navigate(-1)}
            className="rounded-lg border border-neutral-200 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={createMutation.isPending}
            className="rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white hover:bg-[#064A4A] transition-colors disabled:opacity-50"
          >
            {createMutation.isPending ? 'Saving…' : 'Save as Draft'}
          </button>
        </div>
      </div>

      {/* Claim header */}
      <div className="rounded-xl border border-neutral-200 bg-white p-5">
        <h2 className="mb-4 text-sm font-semibold text-neutral-700">Claim Details</h2>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Submission Date</label>
            <input
              type="date"
              value={submissionDate}
              onChange={(e) => setSubmissionDate(e.target.value)}
              required
              className="w-full rounded border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Notes</label>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Optional"
              className="w-full rounded border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Vehicle Description <span className="text-red-500">*</span></label>
            <input
              type="text"
              value={vehicleDesc}
              onChange={(e) => setVehicleDesc(e.target.value)}
              placeholder="e.g. 2022 Honda Civic"
              required
              className="w-full rounded border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Vehicle Owned By</label>
            <select
              value={vehicleOwnedBy}
              onChange={(e) => setVehicleOwnedBy(e.target.value)}
              className="w-full rounded border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
            >
              <option value="self">Self</option>
              <option value="company">Company</option>
            </select>
          </div>
        </div>
      </div>

      {/* Over-km warning */}
      {isOverMaxKm && (
        <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            Total distance ({totalKm.toFixed(1)} km) exceeds the {maxKm.toLocaleString()} km policy limit.
            Finance Manager will review this claim.
          </span>
        </div>
      )}

      {/* Trip log */}
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        <div className="flex items-center justify-between border-b border-neutral-100 px-5 py-3">
          <h2 className="text-sm font-semibold text-neutral-700">Trip Log</h2>
          <span className="text-xs text-neutral-400">Rate: ${parseFloat(currentRate).toFixed(4)}/km</span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 border-b border-neutral-100">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-medium text-neutral-500 w-8">#</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-neutral-500 w-32">Date</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-neutral-500">From</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-neutral-500">To</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-neutral-500">Purpose</th>
                <th className="px-3 py-2 text-center text-xs font-medium text-neutral-500 w-20">Round Trip</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-neutral-500 w-24">km</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-neutral-500 w-28">Amount</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {trips.map((trip, idx) => (
                <tr key={idx} className="border-b border-neutral-100">
                  <td className="px-3 py-2 text-xs text-neutral-400 font-mono">{idx + 1}</td>
                  <td className="px-3 py-2">
                    <input
                      type="date"
                      value={trip.trip_date}
                      onChange={(e) => updateTrip(idx, { trip_date: e.target.value })}
                      className="w-full rounded border border-neutral-200 px-2 py-1.5 text-xs focus:outline-none focus:border-primary-400"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="text"
                      value={trip.from_location}
                      onChange={(e) => updateTrip(idx, { from_location: e.target.value })}
                      placeholder="Starting location"
                      className="w-full rounded border border-neutral-200 px-2 py-1.5 text-xs focus:outline-none focus:border-primary-400"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="text"
                      value={trip.to_location}
                      onChange={(e) => updateTrip(idx, { to_location: e.target.value })}
                      placeholder="Destination"
                      className="w-full rounded border border-neutral-200 px-2 py-1.5 text-xs focus:outline-none focus:border-primary-400"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="text"
                      value={trip.purpose}
                      onChange={(e) => updateTrip(idx, { purpose: e.target.value })}
                      placeholder="Business reason"
                      className="w-full rounded border border-neutral-200 px-2 py-1.5 text-xs focus:outline-none focus:border-primary-400"
                    />
                  </td>
                  <td className="px-3 py-2 text-center">
                    <input
                      type="checkbox"
                      checked={trip.is_round_trip}
                      onChange={(e) => updateTrip(idx, { is_round_trip: e.target.checked })}
                      className="h-4 w-4 rounded accent-primary-600"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="number"
                      step="0.1"
                      min="0"
                      value={trip.distance_km}
                      onChange={(e) => updateTrip(idx, { distance_km: e.target.value })}
                      placeholder="0.0"
                      className="w-full rounded border border-neutral-200 px-2 py-1.5 text-xs text-right font-mono focus:outline-none focus:border-primary-400"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <div className="rounded bg-neutral-50 px-2 py-1.5 text-xs text-right font-mono text-neutral-700">
                      {parseFloat(trip.amount) > 0 ? `$${parseFloat(trip.amount).toFixed(2)}` : '—'}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    {trips.length > 1 && (
                      <button
                        type="button"
                        onClick={() => setTrips((prev) => prev.filter((_, i) => i !== idx).map((t, i) => ({ ...t, trip_number: i + 1 })))}
                        className="rounded p-1 text-neutral-300 hover:text-red-400 transition-colors"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>

            <tfoot>
              <tr className="border-t-2 border-neutral-200 bg-neutral-50">
                <td colSpan={6} className="px-3 py-2 text-xs font-semibold text-neutral-700">Total</td>
                <td className="px-3 py-2 text-right text-xs font-mono font-semibold">{totalKm.toFixed(1)} km</td>
                <td className="px-3 py-2 text-right text-xs font-mono font-semibold text-primary-700">
                  {formatAmount(totalAmount)}
                </td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>

        <div className="border-t border-neutral-100 p-3">
          <button
            type="button"
            onClick={() => setTrips((prev) => [...prev, emptyTrip(prev.length + 1, currentRate, defaultAccountId, null, null)])}
            className="flex items-center gap-2 text-xs text-primary-600 hover:text-primary-700 font-medium"
          >
            <Plus className="h-3.5 w-3.5" />
            Add trip
          </button>
        </div>
      </div>

      {/* Error */}
      {createMutation.isError && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {(createMutation.error as Error).message}
        </div>
      )}
    </form>
  )
}
