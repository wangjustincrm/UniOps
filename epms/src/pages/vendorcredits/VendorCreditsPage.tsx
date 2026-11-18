import { useEffect, useState } from 'react'
import { AlertTriangle, Plus } from 'lucide-react'

import { useRolePermissions } from '@/hooks/useConfig'
import { useReviewVendorCredit, useVendorCredit, useVendorCredits } from '@/hooks/useVendorCredits'
import type { VendorCredit } from '@/services/vendorCredits'
import { useAuthStore } from '@/stores/auth.store'
import { formatDate } from '@/lib/utils'
import { Alert, Button, Modal, Pagination, SortTh, TBody, THead, Table, Tabs, Td, Textarea, Th, Tr, sortParams, useSort, Badge } from '@uniops/shell'

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
          <h1 className="text-xl font-semibold text-fg">Vendor Credits</h1>
          <p className="mt-1 text-sm text-fg-muted">
            Credit notes received from vendors. Approved credits are netted off the next payment to that vendor.
          </p>
        </div>
        <Button size="md" onClick={() => setShowManual(true)}
                title="For a credit the vendor confirmed by email but will not issue a credit note for"
                className="shrink-0">
          <Plus className="h-4 w-4" />
          Record Manual Credit
        </Button>
      </div>

      <div className="mt-4">
        <Tabs items={TABS.map((t) => ({ key: t.key, label: t.label }))} value={tab} onChange={(k) => { setTab(k as typeof tab); setPage(1) }} />
      </div>

      {approveError && !openCredit && (
        <div className="mt-4 flex items-center gap-2 rounded-lg border border-danger-line bg-danger-soft px-3 py-2 text-xs text-danger-fg">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          {approveError}
        </div>
      )}

      {isLoading ? (
        <p className="mt-6 text-sm text-fg-muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="mt-6 text-sm text-fg-muted">No credits in this state.</p>
      ) : (
        <Table className="mt-4">
          <THead>
            <Tr>
              <SortTh label="Credit #" sortKey="credit_number" sort={sort} onSort={onSort} className="px-4 py-2" />
              <SortTh label="Vendor" sortKey="vendor_name" sort={sort} onSort={onSort} className="px-4 py-2" />
              <SortTh label="Vendor Doc #" sortKey="vendor_credit_number" sort={sort} onSort={onSort} className="px-4 py-2" />
              <SortTh label="Date" sortKey="credit_date" sort={sort} onSort={onSort} className="px-4 py-2" />
              <SortTh label="Original" sortKey="total_amount" sort={sort} onSort={onSort} align="right" className="px-4 py-2" />
              <SortTh label="Applied" sortKey="applied_amount" sort={sort} onSort={onSort} align="right" className="px-4 py-2" />
              <SortTh label="Remaining" sortKey="remaining_amount" sort={sort} onSort={onSort} align="right" className="px-4 py-2" />
              <SortTh label="PO" sortKey="po_number" sort={sort} onSort={onSort} className="px-4 py-2" />
              <Th />
            </Tr>
          </THead>
          <TBody>
            {rows.map((c) => (
              <Tr key={c.id} onClick={() => setOpenId(c.id)}
                  className="cursor-pointer hover:bg-surface-subtle">
                <Td className="font-mono text-xs">
                  {c.credit_number}
                  {c.opening_balance && (
                    <Badge size="sm" className="ml-2">
                      Opening balance
                    </Badge>
                  )}
                  {c.source === 'manual' && (
                    <Badge variant="warning" size="sm" className="ml-2" title="No credit note issued — recorded from the vendor's email">
                      Manual
                    </Badge>
                  )}
                </Td>
                <Td>{c.vendor_name}</Td>
                <Td className="font-mono text-xs">{c.vendor_credit_number}</Td>
                <Td>{formatDate(c.credit_date)}</Td>
                <Td align="right" className="font-mono">{fmt(c.total_amount, c.currency)}</Td>
                <Td align="right" className="font-mono">{fmt(c.applied_amount, c.currency)}</Td>
                {/* A voided credit can no longer be applied, so its Remaining
                    reads 0 (ISS-0189). Display only; the stored amount is kept. */}
                <Td align="right" className="font-mono font-semibold">{fmt(c.status === 'void' ? '0' : c.remaining_amount, c.currency)}</Td>
                <Td className="text-xs text-fg-muted">{c.po_number ?? '—'}</Td>
                <Td align="right" onClick={(e) => e.stopPropagation()}>
                  {/* A manual credit's evidence is the attached email, so it is
                      reviewed in the drawer where that file can be opened. */}
                  {canManage && c.status === 'pending_review' && c.source === 'manual' && (
                    <Button variant="outline" size="xs"
                     
                      onClick={() => { review.reset(); setOpenId(c.id) }}>
                      Review
                    </Button>
                  )}
                  {canManage && c.status === 'pending_review' && c.source !== 'manual' && (
                    <>
                      <Button variant="success" size="xs"
                        className="mr-2"
                        disabled={review.isPending}
                        onClick={() => review.mutate({ id: c.id, action: 'approve' })}>
                        Approve
                      </Button>
                      <Button variant="danger-outline" size="xs"
                       
                        onClick={() => { review.reset(); setNoteFor({ credit: c, action: 'reject' }); setNote('') }}>
                        Reject
                      </Button>
                    </>
                  )}
                  {canManage && c.status === 'available' && Number(c.applied_amount) === 0 && (
                    <Button variant="secondary" size="xs"
                     
                      onClick={() => { review.reset(); setNoteFor({ credit: c, action: 'void' }); setNote('') }}>
                      Void
                    </Button>
                  )}
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
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

      {noteFor && (
        <Modal
          open
          onClose={() => setNoteFor(null)}
          // Closed only by its buttons, as before.
          closeOnEscape={false}
          size="sm"
          title={<>{noteFor.action === 'reject' ? 'Reject' : 'Void'} <span className="font-mono">{noteFor.credit.credit_number}</span></>}
          bodyClassName="flex flex-col gap-3"
          footer={<>
              <Button variant="secondary" size="sm"
                      onClick={() => setNoteFor(null)}>
                Cancel
              </Button>
              <Button variant="danger" size="sm"
               
                disabled={!note.trim() || review.isPending}
                onClick={() => {
                  review.mutate(
                    { id: noteFor.credit.id, action: noteFor.action, note: note.trim() },
                    { onSuccess: () => { setNoteFor(null); setOpenId(null) } },
                  )
                }}>
                Confirm
              </Button>
          </>}
        >
            <Textarea
              aria-label="Reason"
              rows={3} value={note} onChange={(e) => setNote(e.target.value)}
              placeholder="Reason (required)" />
            {review.isError && review.variables?.action === noteFor.action && (
              <Alert tone="danger">{errorMessage(review.error, `${noteFor.action === 'reject' ? 'Reject' : 'Void'} failed`)}
              </Alert>
            )}
        </Modal>
      )}
    </div>
  )
}
