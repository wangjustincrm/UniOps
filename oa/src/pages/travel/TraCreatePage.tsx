import { useEffect, useState } from 'react'
import { BackLink } from '@/components/BackLink'
import { useReplaceTab, Button, Checkbox, Input, Textarea, Card } from '@uniops/shell'
import { ArrowLeft } from 'lucide-react'
import { oaRoutes } from '@/app/routes'
import { todayLocal } from '@/lib/utils'
import { useEditableClaim, useSaveClaim, type EditableFormProps } from '@/lib/editableClaim'
import { TravelerPicker, type Traveler } from '@/components/TravelerPicker'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

const TRANSPORT = [
  ['airplane', 'Airplane'], ['train', 'Train'], ['ship', 'Ship'], ['car', 'Car'],
  ['accommodation', 'Accommodation'], ['meal', 'Meal'], ['other', 'Other'],
] as const

const today = todayLocal

export default function TraCreatePage({ editClaimId }: EditableFormProps = {}) {
  const { claim: editing, isLoading: loadingClaim, error: loadError } = useEditableClaim(editClaimId)
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

  // Prefill from the application being edited, keyed on its id so a background
  // refetch cannot wipe out work in progress.
  useEffect(() => {
    if (!editing) return
    setAppDate(editing.submission_date)
    setTravelers(editing.travelers.map(t => ({ user_id: t.user_id, user_name: t.user_name })))
    setDestination(editing.travel_destination ?? '')
    setFromDate(editing.travel_from_date ?? today())
    setToDate(editing.travel_to_date ?? today())
    setReason(editing.purpose ?? '')
    setLeaveFrom(editing.leave_from_date ?? '')
    setLeaveTo(editing.leave_to_date ?? '')
    setModes(editing.transport_modes ?? [])
    setRemarks(editing.notes ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing?.id])

  const toggleMode = (k: string) =>
    setModes(prev => prev.includes(k) ? prev.filter(m => m !== k) : [...prev, k])

  const mutation = useSaveClaim(editClaimId, (id) => replaceTab(`/travel/${id}`))

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (travelers.length === 0) { setError('Add at least one traveler'); return }
    if (!destination.trim()) { setError('Destination is required'); return }
    if (!reason.trim()) { setError('Reason is required'); return }
    setError('')
    mutation.mutate({
      claim_type: 'TRA', submission_date: appDate, currency: 'CAD',
      purpose: reason, notes: remarks || null,
      travel_destination: destination, travel_from_date: fromDate, travel_to_date: toDate,
      transport_modes: modes,
      leave_from_date: leaveFrom || null, leave_to_date: leaveTo || null,
      travelers: travelers.map((t, i) => ({ user_id: t.user_id, user_name: t.user_name, seq: i })),
    }, {
      onError: (e: any) => setError(e.message || (editClaimId
        ? 'Failed to save changes' : 'Failed to create travel application')),
    })
  }

  if (loadingClaim) {
    return <div className="p-8 text-sm text-fg-muted">Loading application…</div>
  }
  if (loadError) {
    return <ErrorBanner message={loadError.message} />
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-6 max-w-3xl">
      <div>
        <BackLink to="/travel" className="inline-flex items-center gap-1.5 text-sm text-fg-muted hover:text-fg-secondary mb-4">
          <ArrowLeft className="h-4 w-4" />Back to Travel Applications
        </BackLink>
        <h1 className="text-2xl font-semibold text-fg">
          {editing ? `Edit ${editing.claim_number}` : 'Travel Application'}
        </h1>
        <p className="mt-0.5 text-sm text-fg-muted">Apply for a business trip before claiming expenses</p>
      </div>

      <Card className="p-5 flex flex-col gap-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-fg-secondary">Application Date *</label>
            <Input type="date" required value={appDate} onChange={e => setAppDate(e.target.value)}
              className="w-full" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-fg-secondary">Number of Persons</label>
            <Input readOnly value={travelers.length}
              className="w-full" />
          </div>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-fg-secondary">Travelers (Staff) *</label>
          <TravelerPicker value={travelers} onChange={setTravelers} />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="sm:col-span-1">
            <label className="mb-1 block text-xs font-medium text-fg-secondary">Destination *</label>
            <Input required value={destination} onChange={e => setDestination(e.target.value)}
              placeholder="e.g. Toronto, ON" className="w-full" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-fg-secondary">From *</label>
            <Input type="date" required value={fromDate} onChange={e => setFromDate(e.target.value)}
              className="w-full" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-fg-secondary">To *</label>
            <Input type="date" required value={toDate} min={fromDate} onChange={e => setToDate(e.target.value)}
              className="w-full" />
          </div>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-fg-secondary">Reasons and Explanation *</label>
          <Textarea required value={reason} onChange={e => setReason(e.target.value)} rows={3}
            className="w-full resize-none" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-fg-secondary">Transportation & Accommodation</label>
          <div className="flex flex-wrap gap-3">
            {TRANSPORT.map(([k, lbl]) => (
              <label key={k} className="inline-flex items-center gap-1.5 text-sm text-fg-secondary">
                <Checkbox checked={modes.includes(k)} onChange={() => toggleMode(k)} />{lbl}
              </label>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-fg-secondary">Leave From (optional)</label>
            <Input type="date" value={leaveFrom} onChange={e => setLeaveFrom(e.target.value)}
              className="w-full" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-fg-secondary">Leave To (optional)</label>
            <Input type="date" value={leaveTo} onChange={e => setLeaveTo(e.target.value)}
              className="w-full" />
          </div>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-fg-secondary">Remarks (external companions, notes)</label>
          <Textarea value={remarks} onChange={e => setRemarks(e.target.value)} rows={2}
            className="w-full resize-none" />
        </div>
      </Card>

      {error && <ErrorBanner message={error} />}
      <Button type="submit" size="sm" disabled={mutation.isPending} className="self-start">
        {mutation.isPending ? 'Saving…' : editing ? 'Save Changes' : 'Save Draft'}
      </Button>
    </form>
  )
}
