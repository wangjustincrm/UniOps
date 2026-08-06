import { useState } from 'react'

import { useRolePermissions } from '@/hooks/useConfig'
import { useReviewVendorCredit, useVendorCredits } from '@/hooks/useVendorCredits'
import type { VendorCredit } from '@/services/vendorCredits'
import { useAuthStore } from '@/stores/auth.store'

const TABS = [
  { key: 'pending_review', label: 'Pending Review' },
  { key: 'available',      label: 'Available' },
  { key: 'exhausted',      label: 'Exhausted' },
  { key: 'void',           label: 'Void' },
] as const

const fmt = (v: string, ccy: string) =>
  `${ccy} ${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export function VendorCreditsPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]['key']>('pending_review')
  const [noteFor, setNoteFor] = useState<{ credit: VendorCredit; action: 'reject' | 'void' } | null>(null)
  const [note, setNote] = useState('')

  const { data, isLoading } = useVendorCredits(tab)
  const review = useReviewVendorCredit()

  const user  = useAuthStore((s) => s.user)
  const perms = useRolePermissions().data?.permissions
  const canManage = user?.role === 'system_admin' || !!perms?.['epms.vendor_credit.manage']

  const rows = data?.items ?? []

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold text-neutral-900">Vendor Credits</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Credit notes received from vendors. Approved credits are netted off the next payment to that vendor.
      </p>

      <div className="mt-4 flex gap-1 border-b border-neutral-200">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
                  className={`px-4 py-2 text-sm font-medium ${
                    tab === t.key
                      ? 'border-b-2 border-primary-600 text-primary-700'
                      : 'text-neutral-500 hover:text-neutral-700'}`}>
            {t.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <p className="mt-6 text-sm text-neutral-500">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="mt-6 text-sm text-neutral-500">No credits in this state.</p>
      ) : (
        <table className="mt-4 w-full text-sm">
          <thead className="text-left text-xs uppercase text-neutral-500">
            <tr>
              <th className="px-4 py-2">Credit #</th>
              <th className="px-4 py-2">Vendor</th>
              <th className="px-4 py-2">Vendor Doc #</th>
              <th className="px-4 py-2">Date</th>
              <th className="px-4 py-2 text-right">Original</th>
              <th className="px-4 py-2 text-right">Applied</th>
              <th className="px-4 py-2 text-right">Remaining</th>
              <th className="px-4 py-2">PO</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} className="border-t border-neutral-100">
                <td className="px-4 py-3 font-mono text-xs">
                  {c.credit_number}
                  {c.opening_balance && (
                    <span className="ml-2 rounded bg-neutral-100 px-1.5 py-0.5 text-[10px] text-neutral-600">
                      Opening balance
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
                <td className="px-4 py-3 text-right">
                  {canManage && c.status === 'pending_review' && (
                    <>
                      <button
                        className="mr-2 rounded bg-success-600 px-2.5 py-1 text-xs font-medium text-white"
                        disabled={review.isPending}
                        onClick={() => review.mutate({ id: c.id, action: 'approve' })}>
                        Approve
                      </button>
                      <button
                        className="rounded border border-danger-300 px-2.5 py-1 text-xs font-medium text-danger-700"
                        onClick={() => { setNoteFor({ credit: c, action: 'reject' }); setNote('') }}>
                        Reject
                      </button>
                    </>
                  )}
                  {canManage && c.status === 'available' && Number(c.applied_amount) === 0 && (
                    <button
                      className="rounded border border-neutral-300 px-2.5 py-1 text-xs font-medium text-neutral-700"
                      onClick={() => { setNoteFor({ credit: c, action: 'void' }); setNote('') }}>
                      Void
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {noteFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-md rounded-lg bg-white p-5">
            <h2 className="text-base font-semibold">
              {noteFor.action === 'reject' ? 'Reject' : 'Void'} {noteFor.credit.credit_number}
            </h2>
            <textarea
              className="mt-3 w-full rounded border border-neutral-300 p-2 text-sm"
              rows={3} value={note} onChange={(e) => setNote(e.target.value)}
              placeholder="Reason (required)" />
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
                    { onSuccess: () => setNoteFor(null) },
                  )
                }}>
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
