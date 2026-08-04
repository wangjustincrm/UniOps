import { useState } from 'react'
import { useReplaceTab } from '@uniops/shell'
import { useMutation } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { oaRoutes } from '@/app/routes'
import { api } from '@/lib/api'
import { TravelerPicker, type Traveler } from '@/components/TravelerPicker'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

const TRANSPORT = [
  ['airplane', 'Airplane'], ['train', 'Train'], ['ship', 'Ship'], ['car', 'Car'],
  ['accommodation', 'Accommodation'], ['meal', 'Meal'], ['other', 'Other'],
] as const

const today = () => new Date().toISOString().slice(0, 10)

export default function TraCreatePage() {
  const replaceTab = useReplaceTab(oaRoutes)
  const [appDate, setAppDate] = useState(today())
  const [travelers, setTravelers] = useState<Traveler[]>([])
  const [destination, setDestination] = useState('')
  const [fromDate, setFromDate] = useState(today())
  const [toDate, setToDate] = useState(today())
  const [reason, setReason] = useState('')
  const [leaveFrom, setLeaveFrom] = useState('')
  const [leaveTo, setLeaveTo] = useState('')
  const [modes, setModes] = useState<string[]>([])
  const [remarks, setRemarks] = useState('')
  const [error, setError] = useState('')

  const toggleMode = (k: string) =>
    setModes(prev => prev.includes(k) ? prev.filter(m => m !== k) : [...prev, k])

  const mutation = useMutation({
    mutationFn: () => api.post<{ id: string }>('/api/v1/expenses', {
      claim_type: 'TRA', submission_date: appDate, currency: 'CAD',
      purpose: reason, notes: remarks || null,
      travel_destination: destination, travel_from_date: fromDate, travel_to_date: toDate,
      transport_modes: modes,
      leave_from_date: leaveFrom || null, leave_to_date: leaveTo || null,
      travelers: travelers.map((t, i) => ({ user_id: t.user_id, user_name: t.user_name, seq: i })),
    }),
    onSuccess: (d) => replaceTab(`/travel/${d.id}`),
    onError: (e: any) => setError(e.message || 'Failed to create travel application'),
  })

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (travelers.length === 0) { setError('Add at least one traveler'); return }
    if (!destination.trim()) { setError('Destination is required'); return }
    if (!reason.trim()) { setError('Reason is required'); return }
    mutation.mutate()
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-6 max-w-3xl">
      <div>
        <a href="/travel" className="inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700 mb-4">
          <ArrowLeft className="h-4 w-4" />Back to Travel Applications
        </a>
        <h1 className="text-2xl font-bold text-neutral-900">Travel Application</h1>
        <p className="mt-0.5 text-sm text-neutral-500">Apply for a business trip before claiming expenses</p>
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white p-5 flex flex-col gap-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Application Date *</label>
            <input type="date" required value={appDate} onChange={e => setAppDate(e.target.value)}
              className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Number of Persons</label>
            <input readOnly value={travelers.length}
              className="w-full rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-500" />
          </div>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-neutral-600">Travelers (Staff) *</label>
          <TravelerPicker value={travelers} onChange={setTravelers} />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="sm:col-span-1">
            <label className="mb-1 block text-xs font-medium text-neutral-600">Destination *</label>
            <input required value={destination} onChange={e => setDestination(e.target.value)}
              placeholder="e.g. Toronto, ON" className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">From *</label>
            <input type="date" required value={fromDate} onChange={e => setFromDate(e.target.value)}
              className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">To *</label>
            <input type="date" required value={toDate} min={fromDate} onChange={e => setToDate(e.target.value)}
              className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm" />
          </div>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-neutral-600">Reasons and Explanation *</label>
          <textarea required value={reason} onChange={e => setReason(e.target.value)} rows={3}
            className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm resize-none" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-neutral-600">Transportation & Accommodation</label>
          <div className="flex flex-wrap gap-3">
            {TRANSPORT.map(([k, lbl]) => (
              <label key={k} className="inline-flex items-center gap-1.5 text-sm text-neutral-700">
                <input type="checkbox" checked={modes.includes(k)} onChange={() => toggleMode(k)} />{lbl}
              </label>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Leave From (optional)</label>
            <input type="date" value={leaveFrom} onChange={e => setLeaveFrom(e.target.value)}
              className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Leave To (optional)</label>
            <input type="date" value={leaveTo} onChange={e => setLeaveTo(e.target.value)}
              className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm" />
          </div>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-neutral-600">Remarks (external companions, notes)</label>
          <textarea value={remarks} onChange={e => setRemarks(e.target.value)} rows={2}
            className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm resize-none" />
        </div>
      </div>

      {error && <ErrorBanner message={error} />}
      <button type="submit" disabled={mutation.isPending}
        className="flex items-center justify-center gap-2 rounded-lg bg-primary-700 px-6 py-2.5 text-sm font-medium text-white hover:bg-primary-800 disabled:opacity-50 self-start">
        {mutation.isPending ? 'Saving…' : 'Save Draft'}
      </button>
    </form>
  )
}
