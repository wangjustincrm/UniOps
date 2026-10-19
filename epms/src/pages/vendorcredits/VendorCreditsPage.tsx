import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Plus } from 'lucide-react'

import { useRolePermissions } from '@/hooks/useConfig'
import { useReviewVendorCredit, useVendorCredit, useVendorCredits } from '@/hooks/useVendorCredits'
import type { VendorCredit } from '@/services/vendorCredits'
import { useAuthStore } from '@/stores/auth.store'
import { Pagination, SortTh, useSort, sortParams } from '@uniops/shell'

type CreditSortKey =
  | 'credit_number' | 'vendor_name' | 'vendor_credit_number' | 'credit_date'
  | 'total_amount' | 'applied_amount' | 'remaining_amount' | 'po_number'

import { CreditDetailDrawer } from './CreditDetailDrawer'
import { ManualCreditModal } from './ManualCreditModal'

const TABS = [
  { key: 'pending_review', label: 'Pending Review' },
  { key: 'available',      label: 'Available' },
  { key: 'exhausted',      label: 'Exhausted' },
  { key: 'void',           label: 'Void' },
] as const

const fmt = (v: string, ccy: string) =>
  `${ccy} ${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const errorMessage = (err: unknown, fallback: string) =>
  err instanceof Error ? err.message : fallback

export function VendorCreditsPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]['key']>('pending_review')
  const [noteFor, setNoteFor] = useState<{ credit: VendorCredit; action: 'reject' | 'void' } | null>(null)
  const [note, setNote] = useState('')
  const [showManual, setShowManual] = useState(false)
  // Held as an id, not the row: after an Add file / review the list refetches
  // and the drawer should show the fresh row, or close once it leaves this tab.
  const [openId, setOpenId] = useState<string | null>(null)
  // The create response of a just-recorded manual credit, shown in the drawer
  // until the by-id fetch lands.
  const [createdCredit, setCreatedCredit] = useState<VendorCredit | null>(null)

  // Paged and sorted by finance-api (limit/offset + sort/dir); a tab or sort
  // change goes back to page 1.
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [sort, onSort] = useSort<CreditSortKey>(null, () => setPage(1))
  const { data, isLoading } = useVendorCredits(tab, { page, pageSize, ...sortParams(sort) })
  const total = data?.total ?? 0
  // A review can move the last row of the last page to another tab.
  useEffect(() => {
    const last = Math.max(1, Math.ceil(total / pageSize))
    if (data && page > last) setPage(last)
  }, [data, total, page, pageSize])
  const review = useReviewVendorCredit()

  const user  = useAuthStore((s) => s.user)
  const perms = useRolePermissions().data?.permissions
  const canManage = user?.role === 'system_admin' || !!perms?.['epms.vendor_credit.manage']

  const rows = data?.items ?? []
  // The open credit is fetched by id, so it shows whatever page or sort is
  // active (a just-created credit need not be on the page in view). The row
  // from the current page is used when present. The drawer still closes once
  // the credit leaves this tab (approve / reject / void).
  const byId = useVendorCredit(openId, createdCredit).data
  const openCredit =
    rows.find((c) => c.id === openId)
    ?? (byId && byId.id === openId && byId.status === tab ? byId : null)

  // The single `review` mutation backs both the inline Approve buttons and the
  // note modal's Confirm button. Route its error to the right spot by looking
  // at which action the failed call was for.
  const approveError =
    review.isError && review.variables?.action === 'approve'
      ? errorMessage(review.error, 'Approve failed')
      : null

  return (
    <div className="p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-neutral-900">Vendor Credits</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Credit notes received from vendors. Approved credits are netted off the next payment to that vendor.
          </p>
        </div>
        <button onClick={() => setShowManual(true)}
                title="For a credit the vendor confirmed by email but will not issue a credit note for"
                className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-2 text-sm font-medium text-white hover:bg-primary-700">
          <Plus className="h-4 w-4" />
          Record Manual Credit
        </button>
      </div>

      <div className="mt-4 flex gap-1 border-b border-neutral-200">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => { setTab(t.key); setPage(1) }}
                  className={`px-4 py-2 text-sm font-medium ${
                    tab === t.key
                      ? 'border-b-2 border-primary-600 text-primary-700'
                      : 'text-neutral-500 hover:text-neutral-700'}`}>
            {t.label}
          </button>
        ))}
      </div>

      {approveError && !openCredit && (
        <div className="mt-4 flex items-center gap-2 rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          {approveError}
        </div>
      )}

      {isLoading ? (
        <p className="mt-6 text-sm text-neutral-500">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="mt-6 text-sm text-neutral-500">No credits in this state.</p>
      ) : (
        <table className="mt-4 w-full text-sm">
          <thead className="text-left text-xs uppercase text-neutral-500">
            <tr>
              <SortTh label="Credit #" sortKey="credit_number" sort={sort} onSort={onSort} className="px-4 py-2" />
              <SortTh label="Vendor" sortKey="vendor_name" sort={sort} onSort={onSort} className="px-4 py-2" />
              <SortTh label="Vendor Doc #" sortKey="vendor_credit_number" sort={sort} onSort={onSort} className="px-4 py-2" />
              <SortTh label="Date" sortKey="credit_date" sort={sort} onSort={onSort} className="px-4 py-2" />
              <SortTh label="Original" sortKey="total_amount" sort={sort} onSort={onSort} align="right" className="px-4 py-2" />
              <SortTh label="Applied" sortKey="applied_amount" sort={sort} onSort={onSort} align="right" className="px-4 py-2" />
              <SortTh label="Remaining" sortKey="remaining_amount" sort={sort} onSort={onSort} align="right" className="px-4 py-2" />
              <SortTh label="PO" sortKey="po_number" sort={sort} onSort={onSort} className="px-4 py-2" />
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} onClick={() => setOpenId(c.id)}
                  className="cursor-pointer border-t border-neutral-100 hover:bg-neutral-50">
                <td className="px-4 py-3 font-mono text-xs">
                  {c.credit_number}
                  {c.opening_balance && (
                    <span className="ml-2 rounded bg-neutral-100 px-1.5 py-0.5 text-[10px] text-neutral-600">
                      Opening balance
                    </span>
                  )}
                  {c.source === 'manual' && (
                    <span className="ml-2 rounded bg-warning-50 px-1.5 py-0.5 text-[10px] text-warning-700"
                          title="No credit note issued — recorded from the vendor's email">
                      Manual
                    </span>
                  )}
                </td>
                <td className="px-4 py-3">{c.vendor_name}</td>
                <td className="px-4 py-3 font-mono text-xs">{c.vendor_credit_number}</td>
                <td className="px-4 py-3">{c.credit_date}</td>
                <td className="px-4 py-3 text-right font-mono">{fmt(c.total_amount, c.currency)}</td>
                <td className="px-4 py-3 text-right font-mono">{fmt(c.applied_amount, c.currency)}</td>
                <td className="px-4 py-3 text-right font-mono font-semibold">{fmt(c.remaining_amount, c.currency)}</td>
                <td className="px-4 py-3 text-xs text-neutral-500">{c.po_number ?? '—'}</td>
                <td className="px-4 py-3 text-right" onClick={(e) => e.stopPropagation()}>
                  {/* A manual credit's evidence is the attached email, so it is
                      reviewed in the drawer where that file can be opened. */}
                  {canManage && c.status === 'pending_review' && c.source === 'manual' && (
                    <button
                      className="rounded border border-primary-300 px-2.5 py-1 text-xs font-medium text-primary-700"
                      onClick={() => { review.reset(); setOpenId(c.id) }}>
                      Review
                    </button>
                  )}
                  {canManage && c.status === 'pending_review' && c.source !== 'manual' && (
                    <>
                      <button
                        className="mr-2 rounded bg-success-600 px-2.5 py-1 text-xs font-medium text-white"
                        disabled={review.isPending}
                        onClick={() => review.mutate({ id: c.id, action: 'approve' })}>
                        Approve
                      </button>
                      <button
                        className="rounded border border-danger-300 px-2.5 py-1 text-xs font-medium text-danger-700"
                        onClick={() => { review.reset(); setNoteFor({ credit: c, action: 'reject' }); setNote('') }}>
                        Reject
                      </button>
                    </>
                  )}
                  {canManage && c.status === 'available' && Number(c.applied_amount) === 0 && (
                    <button
                      className="rounded border border-neutral-300 px-2.5 py-1 text-xs font-medium text-neutral-700"
                      onClick={() => { review.reset(); setNoteFor({ credit: c, action: 'void' }); setNote('') }}>
                      Void
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {total > 0 && (
        <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage}
                    onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
      )}

      {showManual && (
        <ManualCreditModal
          onClose={() => setShowManual(false)}
          onCreated={(credit) => {
            setShowManual(false); setTab('pending_review'); setPage(1)
            setCreatedCredit(credit); setOpenId(credit.id)
          }}
        />
      )}

      {openCredit && (
        <CreditDetailDrawer
          credit={openCredit}
          canManage={canManage}
          reviewPending={review.isPending}
          approveError={approveError}
          onClose={() => { setOpenId(null); review.reset() }}
          onApprove={() => review.mutate({ id: openCredit.id, action: 'approve' },
                                         { onSuccess: () => setOpenId(null) })}
          onReject={() => { review.reset(); setNoteFor({ credit: openCredit, action: 'reject' }); setNote('') }}
          onVoid={() => { review.reset(); setNoteFor({ credit: openCredit, action: 'void' }); setNote('') }}
        />
      )}

      {noteFor && createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-md rounded-lg bg-white p-5">
            <h2 className="text-base font-semibold">
              {noteFor.action === 'reject' ? 'Reject' : 'Void'} {noteFor.credit.credit_number}
            </h2>
            <textarea
              className="mt-3 w-full rounded border border-neutral-300 p-2 text-sm"
              rows={3} value={note} onChange={(e) => setNote(e.target.value)}
              placeholder="Reason (required)" />
            {review.isError && review.variables?.action === noteFor.action && (
              <div className="mt-3 flex items-center gap-2 rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                {errorMessage(review.error, `${noteFor.action === 'reject' ? 'Reject' : 'Void'} failed`)}
              </div>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <button className="rounded border border-neutral-300 px-3 py-1.5 text-sm"
                      onClick={() => setNoteFor(null)}>
                Cancel
              </button>
              <button
                className="rounded bg-danger-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
                disabled={!note.trim() || review.isPending}
                onClick={() => {
                  review.mutate(
                    { id: noteFor.credit.id, action: noteFor.action, note: note.trim() },
                    { onSuccess: () => { setNoteFor(null); setOpenId(null) } },
                  )
                }}>
                Confirm
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
