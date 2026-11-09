/**
 * The safety equipment register: extinguishers, AEDs, eyewash stations,
 * harnesses — and whether each one has been checked.
 *
 * The page opens on what is due rather than on the register, because the
 * register is a reference and the due list is work. Coverage sits at the top
 * for the one question the register exists to answer: an area with three
 * extinguishers, one of which failed its check, has two.
 *
 * A failed check takes the item out of service. That is a state on the asset,
 * not a note on the inspection, because the next person looking for a working
 * extinguisher needs the answer without reading history.
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { AlertTriangle, ClipboardCheck, History, Plus, QrCode, X } from 'lucide-react'
import { api } from '@/lib/api'
import { Pagination, SearchBox, useListView } from '@/components/ListView'
import QrPanel from '@/components/QrPanel'
import { formatDate, formatDateTime } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import { LocationSelect } from '@/components/LocationSelect'
import { VocabularySelect, type VocabItem } from '@/components/VocabularyPicker'
import {
  Card, dangerButton, Empty, ErrorNote, Field, PageHeader, Tabs, inputClass,
  primaryButton, secondaryButton, LoadFailedNote,
} from '@/components/ui'

interface Asset {
  id: string; asset_no: string; name: string
  asset_type_id: string | null; asset_type_label: string | null
  serial_no: string | null; manufacturer: string | null; model: string | null
  location_id: string | null; location_path: string | null; department_name: string | null
  inspection_frequency: string | null; next_due_on: string | null
  inspection_template_id: string | null
  last_inspected_on: string | null; expires_on: string | null
  status: string; notes: string | null; qr_token: string | null
}
interface Inspection {
  id: string; asset_id: string; due_on: string; performed_on: string | null
  performed_by_name: string | null; result: string; notes: string | null
  completed_at: string | null
}
interface Coverage { label: string; in_service: number; out_of_service: number }

const FREQUENCIES = [
  { key: 'monthly', label: 'Monthly' },
  { key: 'quarterly', label: 'Quarterly' },
  { key: 'semiannual', label: 'Twice a year' },
  { key: 'annual', label: 'Yearly' },
]

const TABS = [
  { key: 'due', label: 'Checks due' },
  { key: 'register', label: 'Register' },
] as const
type TabKey = (typeof TABS)[number]['key']

export default function SafetyEquipmentPage() {
  // Scanning an item's QR label lands here with ?asset=<id>. The register is
  // then narrowed to that one item, which is what somebody standing in front
  // of it wants — not a list of four hundred.
  const [params, setParams] = useSearchParams()
  const focused = params.get('asset')
  // ★ And whether the scan was somebody standing at the item about to check
  // it. "Record a check" on the code's landing screen used to open the whole
  // register — four hundred rows, on a phone, at the top of a ladder — which
  // is what the third round reported as scanning not recording a check. The
  // camera knows which item it is; the screen should not ask again.
  const checkNow = params.get('check') === '1'
  const [tab, setTab] = useState<TabKey>(focused ? 'register' : 'due')
  useEffect(() => { if (focused) setTab('register') }, [focused])
  const { can } = usePermissions()
  const mayManage = can('ehs.asset.manage')
  const mayInspect = can('ehs.asset.inspect')

  const assets = useQuery({
    queryKey: ['safety-assets'],
    queryFn: () => api.get<Asset[]>('/api/v1/safety-assets'),
  })
  const checks = useQuery({
    queryKey: ['asset-inspections', 'open'],
    queryFn: () => api.get<Inspection[]>('/api/v1/safety-assets/inspections?result=due&result=missed'),
  })
  const coverage = useQuery({
    queryKey: ['asset-coverage'],
    queryFn: () => api.get<Coverage[]>('/api/v1/safety-assets/coverage'),
  })

  const tabs = TABS.map((t) => t.key === 'due' ? { ...t, count: checks.data?.length } : t)

  return (
    <div className="p-4">
      <PageHeader title="Safety equipment"
                  subtitle="Extinguishers, AEDs, eyewash stations, fall protection" />

      {(coverage.data?.length ?? 0) > 0 && (
        <ul className="mb-4 flex flex-wrap gap-2">
          {coverage.data?.map((c) => (
            <li key={c.label} className="rounded-xl border border-neutral-200 bg-white px-3.5 py-2.5">
              <p className="text-xs text-neutral-500">{c.label}</p>
              <p className="font-mono text-lg font-bold tabular-nums text-neutral-900">
                {c.in_service}
                {c.out_of_service > 0 && (
                  <span className="ml-1.5 text-sm font-semibold text-danger-700">
                    +{c.out_of_service} out of service
                  </span>
                )}
              </p>
            </li>
          ))}
        </ul>
      )}

      <Tabs tabs={tabs} active={tab} onChange={setTab} />

      {tab === 'due' && (
        <DueTab checks={checks.data ?? []} assets={assets.data ?? []} mayInspect={mayInspect} />
      )}
      {tab === 'register' && (
        <RegisterTab
          assets={assets.data ?? []} mayManage={mayManage} mayInspect={mayInspect}
          focusedId={focused}
          checkOnOpen={checkNow}
          onCheckConsumed={() => {
            // ★ The flag is spent as it is used. These pages live in a
            // keep-alive tab, so remembering "already opened for this asset"
            // in component state would mean the second scan of the same
            // extinguisher — which is the daily round, the main path — did
            // nothing at all. Taking it out of the URL instead means arriving
            // with it set always opens, and returning to the tab never does.
            const next = new URLSearchParams(params)
            next.delete('check')
            setParams(next, { replace: true })
          }}
          onClearFocus={() => setParams({}, { replace: true })}
        />
      )}
    </div>
  )
}

function DueTab(
  { checks, assets, mayInspect }: { checks: Inspection[]; assets: Asset[]; mayInspect: boolean },
) {
  const [recording, setRecording] = useState<Inspection | null>(null)
  const assetOf = (id: string) => assets.find((a) => a.id === id)

  if (checks.length === 0) {
    return (
      <Empty>
        Nothing due. Checks appear here a week before they are owed, and stay
        until somebody records the result.
      </Empty>
    )
  }

  return (
    <>
      <ul className="flex flex-col gap-2">
        {checks.map((c) => {
          const asset = assetOf(c.asset_id)
          const overdue = c.result === 'missed'
          return (
            <li key={c.id}>
              <Card className={overdue ? '!border-danger-500 !bg-danger-50' : ''}>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-neutral-900">
                      {asset?.name ?? 'Equipment'}
                    </span>
                    <span className="mt-0.5 block text-xs text-neutral-600">
                      {asset?.asset_no}
                      {asset?.location_path && ` · ${asset.location_path}`}
                    </span>
                    <span className={`mt-0.5 block text-xs font-medium ${
                      overdue ? 'text-danger-700' : 'text-neutral-500'}`}>
                      {overdue ? 'Overdue since ' : 'Due '}{formatDate(c.due_on)}
                    </span>
                  </span>
                  {mayInspect && (
                    <button type="button" className={secondaryButton}
                            onClick={() => setRecording(c)}>
                      <ClipboardCheck className="h-4 w-4" aria-hidden />
                      Record
                    </button>
                  )}
                </div>
              </Card>
            </li>
          )
        })}
      </ul>

      {recording && (
        <RecordDialog inspection={recording} asset={assetOf(recording.asset_id)}
                      onClose={() => setRecording(null)} />
      )}
    </>
  )
}

function RecordDialog(
  { inspection, asset, onClose }:
  { inspection: Inspection; asset: Asset | undefined; onClose: () => void },
) {
  const queryClient = useQueryClient()
  const [notes, setNotes] = useState('')

  const record = useMutation({
    mutationFn: (result: 'passed' | 'failed') =>
      api.post(`/api/v1/safety-assets/inspections/${inspection.id}/record`,
               { result, notes: notes || null }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['asset-inspections'] })
      queryClient.invalidateQueries({ queryKey: ['safety-assets'] })
      queryClient.invalidateQueries({ queryKey: ['asset-coverage'] })
      onClose()
    },
  })

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center bg-black/40 p-4 sm:items-center">
      <div role="dialog" aria-modal="true" aria-label="Record a check"
           className="w-full max-w-md rounded-xl bg-white p-4 shadow-xl">
        <div className="mb-3 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-neutral-900">{asset?.name}</h2>
            <p className="text-xs text-neutral-500">
              {asset?.asset_no} · due {formatDate(inspection.due_on)}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close"
                  className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
            <X className="h-4 w-4" />
          </button>
        </div>

        <Field label="Notes" hint="What you found. Required reading if it failed.">
          <textarea className={`${inputClass} min-h-[80px] py-2`} value={notes}
                    onChange={(e) => setNotes(e.target.value)} />
        </Field>

        <ErrorNote error={record.error} />

        <div className="mt-3 flex flex-col gap-2 sm:flex-row-reverse">
          <button type="button"
                  className={`${primaryButton} flex-1 !bg-success-600 hover:!bg-success-700`}
                  disabled={record.isPending}
                  onClick={() => { if (!record.isPending) record.mutate('passed') }}>
            Passed
          </button>
          <button type="button"
                  className={`${primaryButton} flex-1 !bg-danger-600 hover:!bg-danger-700`}
                  disabled={record.isPending || !notes.trim()}
                  onClick={() => { if (!record.isPending) record.mutate('failed') }}>
            <AlertTriangle className="h-4 w-4" aria-hidden />
            Failed
          </button>
        </div>
        <p className="mt-2 text-xs text-neutral-500">
          Recording a failure takes this item out of service until somebody puts
          it back.
        </p>
      </div>
    </div>
  )
}

function RegisterTab({
  assets, mayManage, mayInspect, focusedId, checkOnOpen, onCheckConsumed, onClearFocus,
}: {
  assets: Asset[]; mayManage: boolean; mayInspect: boolean
  focusedId?: string | null; checkOnOpen?: boolean
  onCheckConsumed?: () => void; onClearFocus?: () => void
}) {
  const queryClient = useQueryClient()
  const [codeFor, setCodeFor] = useState<string | null>(null)
  // "Record a check is not logged anywhere once complete." It was logged —
  // the row is written and the asset's last-checked date moves — but the only
  // list on this screen asks for what is still due, so a completed check left
  // that list and appeared nowhere. A check nobody can see afterwards is not
  // evidence, and evidence is the whole point of the register.
  const [historyFor, setHistoryFor] = useState<string | null>(null)
  // A check somebody did, whether or not one was due. Recording was only
  // possible against an occurrence the sweep had raised, and with nothing due
  // on anything, pressing the button saved nothing.
  const [checkingNow, setCheckingNow] = useState<Asset | null>(null)
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<Asset | null>(null)
  // ★ Round 12, Q3: a retired item leaves the working list; it is one press away.
  const [showRetired, setShowRetired] = useState(false)
  const retiredCount = assets.filter((a) => a.status === 'retired').length
  const shown = focusedId
    ? assets.filter((a) => a.id === focusedId)
    : assets.filter((a) => showRetired || a.status !== 'retired')

  // The item a code was just scanned on, once it has arrived from the server.
  const scanned = checkOnOpen && focusedId && mayInspect
    ? shown.find((a) => a.id === focusedId)
    : undefined
  // Said in words rather than by silence: a scan that opens nothing looks
  // like a scan that failed, and "this one is out of service" is the answer.
  const [blocked, setBlocked] = useState<Asset | null>(null)
  useEffect(() => {
    if (!scanned) return
    if (scanned.status === 'in_service') setCheckingNow(scanned)
    else setBlocked(scanned)
    onCheckConsumed?.()
    // `onCheckConsumed` is written inline by the caller and would change every
    // render; the asset that arrived is what decides this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanned?.id, scanned?.status])
  const list = useListView(shown, (a) => [
    a.asset_no, a.name, a.asset_type_label, a.serial_no, a.location_path, a.status,
  ])

  // Round 10: put back by hand says why; it is kept in the item's history
  // beside the ones put back when their corrective actions were verified.
  const [restoring, setRestoring] = useState<{ id: string; reason: string } | null>(null)
  const restore = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.post(`/api/v1/safety-assets/${id}/return-to-service`, { reason }),
    onSuccess: () => {
      setRestoring(null)
      queryClient.invalidateQueries({ queryKey: ['safety-assets'] })
      queryClient.invalidateQueries({ queryKey: ['asset-history'] })
      queryClient.invalidateQueries({ queryKey: ['asset-coverage'] })
    },
  })

  return (
    <>
      {blocked && (
        <p className="mb-3 rounded-lg bg-warning-50 px-3 py-2 text-sm text-warning-700">
          {blocked.name} is out of service, so a check cannot be recorded against
          it. Put it back in service first, or report what you found.
        </p>
      )}

      {focusedId && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-neutral-100 px-3 py-2">
          <span className="text-xs text-neutral-600">
            Showing one item, from the code you scanned.
          </span>
          <button type="button" onClick={onClearFocus}
                  className="text-xs font-semibold text-primary-700 hover:underline">
            Show the whole register
          </button>
        </div>
      )}

      {mayManage && !focusedId && (
        <div className="mb-3 flex justify-end">
          <button type="button" className={primaryButton} onClick={() => setAdding(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            Register equipment
          </button>
        </div>
      )}
      {adding && <AssetForm onClose={() => setAdding(false)} />}
      {editing && <AssetForm existing={editing} onClose={() => setEditing(null)} />}

      <SearchBox value={list.query} onChange={list.setQuery}
                 placeholder="Search by number, what it is, serial or area">
        {!focusedId && retiredCount > 0 && (
          <label className="flex min-h-[44px] shrink-0 items-center gap-2 text-sm text-neutral-700">
            <input type="checkbox" checked={showRetired}
                   onChange={(e) => setShowRetired(e.target.checked)} />
            Show retired ({retiredCount})
          </label>
        )}
      </SearchBox>

      {shown.length === 0 && !adding ? (
        <Empty>
          {focusedId
            ? 'That item is no longer in the register — the label may be from something that has been removed.'
            : 'Nothing registered. Adding an item puts it on a check cycle and gives it a QR code for the label.'}
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {list.pageRows.map((a) => (
            <li key={a.id}>
              <Card className={a.status === 'out_of_service' ? '!border-danger-300' : ''}>
                <div className="flex flex-wrap items-start gap-x-3 gap-y-1.5">
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-neutral-900">{a.name}</span>
                    <span className="mt-0.5 block text-xs text-neutral-500">
                      {a.asset_no}
                      {a.asset_type_label && ` · ${a.asset_type_label}`}
                      {a.serial_no && ` · s/n ${a.serial_no}`}
                    </span>
                    {a.location_path && (
                      <span className="block text-xs text-neutral-500">{a.location_path}</span>
                    )}
                    <span className="mt-0.5 block text-xs text-neutral-600">
                      {a.inspection_frequency
                        ? `${FREQUENCIES.find((f) => f.key === a.inspection_frequency)?.label ?? a.inspection_frequency}`
                        : 'Not on a check cycle'}
                      {a.next_due_on && ` · next ${formatDate(a.next_due_on)}`}
                      {a.last_inspected_on && ` · last ${formatDate(a.last_inspected_on)}`}
                    </span>
                  </span>

                  {mayManage && (
                    <button
                      type="button"
                      onClick={() => setEditing(a)}
                      className="flex min-h-[32px] shrink-0 items-center gap-1 rounded-md px-1.5 text-xs font-medium text-neutral-500 hover:bg-neutral-50 hover:text-neutral-900"
                    >
                      Edit
                    </button>
                  )}

                  {mayInspect && a.status === 'in_service' && (
                    <button
                      type="button"
                      onClick={() => setCheckingNow(a)}
                      className="flex min-h-[32px] shrink-0 items-center gap-1 rounded-md px-1.5 text-xs font-medium text-primary-700 hover:bg-primary-50"
                    >
                      Record a check
                    </button>
                  )}

                  <button
                    type="button"
                    onClick={() => setHistoryFor(historyFor === a.id ? null : a.id)}
                    aria-expanded={historyFor === a.id}
                    className="flex min-h-[32px] shrink-0 items-center gap-1 rounded-md px-1.5 text-xs text-neutral-500 hover:bg-neutral-50 hover:text-neutral-800"
                  >
                    <History className="h-3.5 w-3.5" aria-hidden />
                    Checks
                  </button>

                  {a.qr_token && (
                    <button
                      type="button"
                      onClick={() => setCodeFor(codeFor === a.id ? null : a.id)}
                      aria-expanded={codeFor === a.id}
                      title="Show the code to print for this item"
                      className="flex min-h-[32px] shrink-0 items-center gap-1 rounded-md px-1.5 text-xs text-neutral-500 hover:bg-neutral-50 hover:text-neutral-800"
                    >
                      <QrCode className="h-4 w-4" aria-hidden />
                      Code
                    </button>
                  )}

                  {a.status === 'out_of_service' ? (
                    <span className="flex shrink-0 items-center gap-2">
                      <span className="rounded-full bg-danger-50 px-2 py-0.5 text-[11px] font-semibold text-danger-700">
                        Out of service
                      </span>
                      {mayManage && restoring?.id !== a.id && (
                        <button type="button" className={secondaryButton}
                                onClick={() => setRestoring({ id: a.id, reason: '' })}>
                          Back in service
                        </button>
                      )}
                    </span>
                  ) : a.status === 'retired' && (
                    <span className="shrink-0 rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-600">
                      Retired
                    </span>
                  )}
                </div>
                {restoring?.id === a.id && (
                  <div className="mt-3 flex flex-col gap-2 border-t border-neutral-100 pt-3">
                    <label className="text-xs font-medium text-neutral-700">
                      Why is it back in service?
                      <textarea rows={2} className={`${inputClass} mt-1 py-2`} value={restoring.reason}
                                onChange={(e) => setRestoring({ id: a.id, reason: e.target.value })}
                                placeholder="Repaired, replaced, or checked again and found sound" />
                    </label>
                    <div className="flex justify-end gap-2">
                      <button type="button" className={secondaryButton} disabled={restore.isPending}
                              onClick={() => setRestoring(null)}>Cancel</button>
                      <button type="button" className={primaryButton}
                              disabled={restoring.reason.trim().length < 5 || restore.isPending}
                              onClick={() => {
                                if (!restore.isPending) restore.mutate({ id: a.id, reason: restoring.reason.trim() })
                              }}>
                        {restore.isPending ? 'Saving…' : 'Put it back in service'}
                      </button>
                    </div>
                  </div>
                )}
                {historyFor === a.id && <CheckHistory assetId={a.id} />}
                {codeFor === a.id && (
                  <div className="mt-3 border-t border-neutral-100 pt-3">
                    <QrPanel
                      token={a.qr_token}
                      caption={a.name}
                      sub={`${a.asset_no}${a.location_path ? ` · ${a.location_path}` : ''}`}
                    />
                  </div>
                )}
              </Card>
            </li>
          ))}
        </ul>
      )}

      <Pagination page={list.page} pageCount={list.pageCount} total={list.total}
                  onPage={list.setPage} noun="items" />
      {checkingNow && (
        <AdHocCheck asset={checkingNow} onClose={() => setCheckingNow(null)} />
      )}
      <ErrorNote error={restore.error} />
    </>
  )
}

/**
 * Registering a piece of equipment, and correcting its record.
 *
 * An extinguisher gets moved, a harness's serial is typed wrong, a cycle set
 * to monthly turns out to be quarterly. The PATCH endpoint was there from the
 * start with nothing on screen reaching it, so the only way to fix any of that
 * was to register the item a second time — which puts two of it on the
 * register, each with its own QR label, and a coverage count that says the
 * plant has more extinguishers than it has.
 */
/**
 * Every check done on one item, most recent first.
 *
 * Read-only and deliberately plain: this is what somebody is pointed at when
 * they ask whether the extinguisher by the dryer has actually been looked at
 * this year, and an auditor asks it in exactly those words.
 */
function CheckHistory({ assetId }: { assetId: string }) {
  const checks = useQuery({
    queryKey: ['asset-inspections', assetId],
    queryFn: () => api.get<Inspection[]>(
      `/api/v1/safety-assets/inspections?asset_id=${assetId}`),
  })

  if (checks.isLoading) {
    return <p className="mt-3 border-t border-neutral-100 pt-3 text-xs text-neutral-500">Loading…</p>
  }

  const done = (checks.data ?? []).filter((c) => c.performed_on)

  return (
    <div className="mt-3 border-t border-neutral-100 pt-3">
      {/* ★ "No check has been recorded" is what Regulation 851 asks about, so
          it must not be what a failed request looks like. */}
      {checks.isError ? (
        <LoadFailedNote what="This item's check history" />
      ) : done.length === 0 ? (
        <p className="text-xs text-neutral-500">
          No check has been recorded against this item yet.
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {done.map((c) => (
            <li key={c.id} className="flex flex-wrap items-baseline gap-x-2 text-xs">
              <span className="font-medium tabular-nums text-neutral-800">
                {formatDate(c.performed_on)}
              </span>
              <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold capitalize ${
                c.result === 'failed' ? 'bg-danger-50 text-danger-700'
                                      : 'bg-success-50 text-success-700'}`}>
                {c.result === 'failed' ? 'Failed' : 'Passed'}
              </span>
              {c.performed_by_name && (
                <span className="text-neutral-500">by {c.performed_by_name}</span>
              )}
              {c.notes && <span className="min-w-0 text-neutral-600">— {c.notes}</span>}
            </li>
          ))}
        </ul>
      )}
      <ServiceHistory assetId={assetId} />
    </div>
  )
}

interface StatusEvent {
  status: string; at: string; by_name: string | null; reason: string | null; action_id: string | null
}

/** ★ Round 10 (open box): "log the date and time and by whom on the equipment
 *  register" — each time it went out of service and came back. */
function ServiceHistory({ assetId }: { assetId: string }) {
  const events = useQuery({
    queryKey: ['asset-history', assetId],
    queryFn: () => api.get<StatusEvent[]>(`/api/v1/safety-assets/${assetId}/history`),
  })
  if (events.isError) return <LoadFailedNote what="When this item was out of service" />
  if (!events.data?.length) return null
  return (
    <div className="mt-3">
      <p className="mb-1 text-xs font-semibold text-neutral-700">In and out of service</p>
      <ul className="flex flex-col gap-1.5">
        {events.data.map((e, n) => (
          <li key={n} className="flex flex-wrap items-baseline gap-x-2 text-xs">
            <span className="font-medium tabular-nums text-neutral-800">{formatDateTime(e.at)}</span>
            <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${
              e.status === 'in_service' ? 'bg-success-50 text-success-700'
                : e.status === 'retired' ? 'bg-neutral-100 text-neutral-600'
                : 'bg-danger-50 text-danger-700'}`}>
              {e.status === 'in_service' ? 'Back in service'
                : e.status === 'retired' ? 'Retired' : 'Out of service'}
            </span>
            {e.by_name && <span className="text-neutral-500">by {e.by_name}</span>}
            {e.reason && <span className="min-w-0 text-neutral-600">— {e.reason}</span>}
            {e.action_id && (
              <Link to={`/actions/${e.action_id}`} className="font-medium text-primary-700 hover:underline">
                the action
              </Link>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}


interface FormTemplate {
  id: string; name: string; code: string; published: boolean; fail_criteria: number
}

function AssetForm({ existing, onClose }: { existing?: Asset; onClose: () => void }) {
  const queryClient = useQueryClient()
  const editing = Boolean(existing)
  const [name, setName] = useState(existing?.name ?? '')
  const [type, setType] = useState<VocabItem | null>(
    existing?.asset_type_id
      ? { id: existing.asset_type_id, label: existing.asset_type_label ?? '' } as VocabItem
      : null)
  const [locationId, setLocationId] = useState<string | null>(existing?.location_id ?? null)
  const [serial, setSerial] = useState(existing?.serial_no ?? '')
  const [frequency, setFrequency] = useState(existing?.inspection_frequency ?? 'monthly')
  const [expiresOn, setExpiresOn] = useState(existing?.expires_on ?? '')
  // ★ Which form this item is checked on. The column has been on the record
  // since the first release, the API accepted it and nothing ever set it —
  // eleven items on the dev database, none linked. Round 4 asked for exactly
  // this: "allow for an equipment qr code to be linked to a specific form."
  const [templateId, setTemplateId] = useState<string | null>(
    existing?.inspection_template_id ?? null)
  const templates = useQuery({
    queryKey: ['forms', 'templates'],
    queryFn: () => api.get<FormTemplate[]>('/api/v1/forms/templates'),
    staleTime: 10 * 60_000,
  })
  const chosen = (templates.data ?? []).find((t) => t.id === templateId)

  const body = () => ({
    name,
    asset_type_id: type?.id ?? null,
    asset_type_label: type?.label ?? null,
    location_id: locationId,
    serial_no: serial || null,
    inspection_frequency: frequency || null,
    expires_on: expiresOn || null,
    inspection_template_id: templateId,
  })

  const create = useMutation({
    mutationFn: () => (editing
      ? api.patch(`/api/v1/safety-assets/${existing!.id}`, body())
      : api.post('/api/v1/safety-assets', body())),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['safety-assets'] })
      queryClient.invalidateQueries({ queryKey: ['asset-coverage'] })
      onClose()
    },
  })

  // ★ Round 12, Q3: retire (off its cycle, history kept) and delete (only
  // before anything has been recorded against it — the server decides).
  const [retiring, setRetiring] = useState(false)
  const [reason, setReason] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const done = () => {
    queryClient.invalidateQueries({ queryKey: ['safety-assets'] })
    queryClient.invalidateQueries({ queryKey: ['asset-coverage'] })
    queryClient.invalidateQueries({ queryKey: ['asset-history'] })
    onClose()
  }
  const retire = useMutation({
    mutationFn: () => api.post(
      `/api/v1/safety-assets/${existing!.id}/${existing!.status === 'retired' ? 'reinstate' : 'retire'}`,
      { reason: reason.trim() || null }),
    onSuccess: done,
  })
  const remove = useMutation({
    mutationFn: () => api.del(`/api/v1/safety-assets/${existing!.id}`),
    onSuccess: done,
    onError: () => setConfirmDelete(false),
  })
  const busy = create.isPending || retire.isPending || remove.isPending

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">
          {editing ? `Correct ${existing!.asset_no}` : 'Register equipment'}
        </h2>
        <button type="button" onClick={onClose} aria-label="Close"
                className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="What it is">
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)}
                 placeholder="Extinguisher — ABC 10lb" />
        </Field>
        <Field label="Kind">
          <VocabularySelect vocabulary="safety_asset_type" value={type?.id ?? null}
                            onChange={setType} />
        </Field>
        <Field label="Where it is">
          <LocationSelect value={locationId} onChange={setLocationId} />
        </Field>
        <Field label="Serial number">
          <input className={inputClass} value={serial} onChange={(e) => setSerial(e.target.value)} />
        </Field>
        <Field label="Checked"
               hint={editing
                 ? 'Changing this moves when the next check falls due'
                 : 'The first check falls one period from today'}>
          <select className={inputClass} value={frequency}
                  onChange={(e) => setFrequency(e.target.value)}>
            {FREQUENCIES.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
            <option value="">Not on a cycle</option>
          </select>
        </Field>
        <Field label="Service life ends"
               hint="A harness is condemned at ten years whatever its condition">
          <input type="date" className={inputClass} value={expiresOn}
                 onChange={(e) => setExpiresOn(e.target.value)} />
        </Field>
        <div className="sm:col-span-2">
          <Field label="Checked on this form"
                 hint="Scanning this item's code opens it, with the item's name on it. Leave it unset for the quick pass/fail dialog.">
            <select className={inputClass} value={templateId ?? ''}
                    onChange={(e) => setTemplateId(e.target.value || null)}>
              <option value="">No form — record pass or fail directly</option>
              {(templates.data ?? [])
                .filter((t) => t.published)
                .map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                    {t.fail_criteria > 0
                      ? ` — ${t.fail_criteria} pass/fail question${t.fail_criteria === 1 ? '' : 's'}`
                      : ' — no pass/fail questions'}
                  </option>
                ))}
            </select>
          </Field>
          {/* ★ Round 4: "there should be a pass of fail criteria set to ensure
              the item is inspected properly." A template with none can be filled
              in and submitted and will always record a pass, whatever was
              observed — so the number is on the option and the warning is here. */}
          {chosen && chosen.fail_criteria === 0 && (
            <p className="mt-1 rounded-lg bg-warning-50 px-2.5 py-2 text-xs text-warning-800">
              <b>{chosen.name}</b> has no pass/fail questions, so a check on it
              always records a pass whatever was seen. Add a fail condition to at
              least one question under Settings → Forms.
            </p>
          )}
        </div>
      </div>
      <ErrorNote error={create.error ?? retire.error ?? remove.error} />
      {existing && retiring && (
        <label className="mt-3 block text-xs font-medium text-neutral-700">
          {existing.status === 'retired' ? 'Why is it back?' : 'Why is it being retired?'}
          <input className={`${inputClass} mt-1`} value={reason}
                 onChange={(e) => setReason(e.target.value)}
                 placeholder={existing.status === 'retired'
                   ? 'Retired by mistake' : 'Replaced, condemned, removed from the plant'} />
        </label>
      )}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {existing && !retiring && (
            <button type="button" className={secondaryButton} disabled={busy}
                    onClick={() => setRetiring(true)}>
              {existing.status === 'retired' ? 'Put back in service' : 'Retire'}
            </button>
          )}
          {existing && retiring && (
            <>
              <button type="button" className={primaryButton} disabled={busy}
                      onClick={() => { if (!busy) retire.mutate() }}>
                {retire.isPending ? 'Saving…'
                  : existing.status === 'retired' ? 'Put it back' : 'Retire it'}
              </button>
              <button type="button" className={secondaryButton}
                      onClick={() => setRetiring(false)}>Not now</button>
            </>
          )}
          {existing && !retiring && !confirmDelete && (
            <button type="button" disabled={busy} onClick={() => setConfirmDelete(true)}
                    className="min-h-[44px] px-2 text-sm font-medium text-danger-700 hover:underline disabled:opacity-50">
              Delete
            </button>
          )}
          {existing && confirmDelete && (
            <span className="flex flex-wrap items-center gap-2 text-xs text-neutral-700">
              Delete for good? Only possible before anything is recorded against it.
              <button type="button" disabled={busy}
                      onClick={() => { if (!busy) remove.mutate() }}
                      className="min-h-[36px] rounded-md bg-danger-600 px-3 font-semibold text-white disabled:opacity-50">
                {remove.isPending ? 'Deleting…' : 'Delete'}
              </button>
              <button type="button" className="min-h-[36px] px-2 font-medium text-neutral-600"
                      onClick={() => setConfirmDelete(false)}>Keep it</button>
            </span>
          )}
        </div>
        <div className="flex gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!name.trim() || busy}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending
            ? (editing ? 'Saving…' : 'Registering…')
            : (editing ? 'Save' : 'Register')}
        </button>
        </div>
      </div>
    </Card>
  )
}

/** Recording a check done now. The schedule says when one is owed, not when
 *  one may happen. */
function AdHocCheck({ asset, onClose }: { asset: Asset; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [notes, setNotes] = useState('')
  const record = useMutation({
    mutationFn: (result: 'passed' | 'failed') =>
      api.post(`/api/v1/safety-assets/${asset.id}/inspections`,
               { result, notes: notes || null }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['safety-assets'] })
      queryClient.invalidateQueries({ queryKey: ['asset-inspections'] })
      onClose()
    },
  })

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center bg-black/40 sm:items-center sm:p-4">
      <div role="dialog" aria-modal="true" aria-label="Record a check"
           className="w-full max-w-md rounded-t-2xl bg-white p-4 sm:rounded-2xl">
        <h2 className="text-sm font-semibold text-neutral-900">{asset.name}</h2>
        <p className="mt-0.5 text-xs text-neutral-500">
          {asset.asset_no}{asset.location_path ? ` · ${asset.location_path}` : ''}
        </p>
        <Field label="Notes" hint="What you saw. Kept on the register.">
          <input className={inputClass} value={notes}
                 onChange={(e) => setNotes(e.target.value)}
                 placeholder="Gauge in the green, seal intact" />
        </Field>
        <p className="mt-2 text-xs text-neutral-500">
          Failing it takes the item out of service and raises a corrective
          action, which is the point of saying so.
        </p>
        <ErrorNote error={record.error} />
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
          <button type="button" className={dangerButton} disabled={record.isPending}
                  onClick={() => { if (!record.isPending) record.mutate('failed') }}>
            Failed
          </button>
          <button type="button" className={primaryButton} disabled={record.isPending}
                  onClick={() => { if (!record.isPending) record.mutate('passed') }}>
            {record.isPending ? 'Recording…' : 'Passed'}
          </button>
        </div>
      </div>
    </div>
  )
}
