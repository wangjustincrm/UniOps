/**
 * Remittance advice panel — who will be emailed for a completed payment or
 * batch. Shared by the Payments hub page and the Payment Batches page
 * (Tasks 12/13): both drop in `<RemittancePanel scope={...} />` directly, or
 * via `<RemittanceDialog>`. `RemittanceStatusBadge` is exported here so
 * those pages reuse the same status pill rather than writing a second one —
 * there is no `StatusBadge` in `@uniops/shell`; domain status→variant
 * mapping stays in the app (see `JvStatusBadge` in JvDetailModal.tsx, which
 * this follows).
 *
 * Preview is live: every fetch (including Refresh) recomputes who is
 * sendable from the current state of vendor emails / invoice numbers — it is
 * a plain re-fetch, not a cache bust. Blocked payees cannot be selected; the
 * disabled checkbox is a convenience only, the server refuses them anyway.
 */
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, RefreshCw, Send } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  fetchPreview, sendRemittance,
  type PayeeGroup, type RemittanceScope, type SendResult,
} from '@/services/remittance'

const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'

export type RemittanceStatus = 'ready' | 'blocked' | 'sent' | 'failed' | 'skipped'

const STATUS_STYLE: Record<RemittanceStatus, string> = {
  ready: 'bg-neutral-100 text-neutral-600',
  blocked: 'bg-amber-50 text-amber-700',
  sent: 'bg-green-50 text-green-700',
  failed: 'bg-red-50 text-red-700',
  skipped: 'bg-neutral-100 text-neutral-500',
}

const STATUS_LABEL: Record<RemittanceStatus, string> = {
  ready: 'Ready', blocked: 'Blocked', sent: 'Sent', failed: 'Failed', skipped: 'Skipped',
}

/** Domain status pill for a remittance payee/result. Follows JvStatusBadge's
 * local-badge convention (see JvDetailModal.tsx) rather than a shared
 * `StatusBadge` from `@uniops/shell`, which does not exist. */
export function RemittanceStatusBadge({ status }: { status: RemittanceStatus }) {
  return (
    <span className={cn('inline-flex rounded-full px-2 py-0.5 text-xs font-medium', STATUS_STYLE[status])}>
      {STATUS_LABEL[status]}
    </span>
  )
}

const BLOCK_REASON_LABEL: Record<string, string> = {
  missing_email: 'Missing email',
  missing_invoice_no: 'Missing invoice number',
}

function blockReasonText(reasons: string[]): string {
  return reasons.map((r) => BLOCK_REASON_LABEL[r] ?? r).join(', ')
}

function payeeKey(kind: string, id: string): string {
  return `${kind}:${id}`
}

function fmtMoney(v: string, ccy: string): string {
  return `${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${ccy}`
}

function readinessOf(g: PayeeGroup): RemittanceStatus {
  if (g.block_reasons.length > 0) return 'blocked'
  if (g.last_send?.status === 'sent') return 'sent'
  if (g.last_send?.status === 'failed') return 'failed'
  if (g.last_send?.status === 'skipped') return 'skipped'
  return 'ready'
}

export function RemittancePanel({ scope, onSent }: {
  scope: RemittanceScope
  onSent?: (result: SendResult) => void
}) {
  const qc = useQueryClient()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const [lastResult, setLastResult] = useState<SendResult | null>(null)

  const queryKey = ['remittance-preview', scope.kind, scope.id] as const
  const { data: preview, isLoading, isFetching, error, refetch } = useQuery({
    queryKey,
    queryFn: () => fetchPreview(scope),
  })

  // Preview is recomputed live on every fetch — default the selection to
  // "every non-blocked payee" each time a fresh preview lands, so a payee
  // that just became sendable (email filled in, invoice number attached
  // since the last look) is selected rather than silently left out.
  useEffect(() => {
    if (!preview) return
    setSelected(new Set(
      preview.groups.filter((g) => g.block_reasons.length === 0)
        .map((g) => payeeKey(g.recipient_kind, g.party_id)),
    ))
  }, [preview])

  async function handleSend() {
    if (!preview) return
    const recipients = preview.groups
      .filter((g) => selected.has(payeeKey(g.recipient_kind, g.party_id)))
      .map((g) => ({ recipient_kind: g.recipient_kind, party_id: g.party_id }))
    if (recipients.length === 0) return
    setSending(true)
    setSendError(null)
    try {
      const result = await sendRemittance(scope, recipients)
      setLastResult(result)
      onSent?.(result)
      // Re-fetch rather than locally patch state — a send just changed the
      // last_send row the server reads, and preview is defined to always
      // reflect that live.
      await qc.invalidateQueries({ queryKey })
    } catch (e) {
      setSendError((e as Error).message)
    } finally {
      setSending(false)
    }
  }

  if (isLoading) {
    return <div className="py-8 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
  }

  if (error) {
    return (
      <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
        {(error as Error).message || 'Failed to load remittance preview'}
      </div>
    )
  }

  if (!preview) return null

  if (!preview.enabled) {
    return (
      <p className="text-sm text-neutral-500">
        Remittance email is not configured. Ask an administrator to enable it in Company Settings.
      </p>
    )
  }

  const selectedCount = selected.size

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm text-neutral-600">
          Reference: <span className="font-mono">{preview.reference}</span>
          <span className="mx-2 text-neutral-300">·</span>
          {preview.payment_method}
        </div>
        <button type="button" onClick={() => void refetch()} disabled={isFetching} className={secondaryBtn}>
          <RefreshCw className={cn('h-4 w-4', isFetching && 'animate-spin')} />
          Refresh
        </button>
      </div>

      {sendError && <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{sendError}</div>}

      {lastResult && (
        <div className="rounded-md bg-neutral-50 px-3 py-2 text-sm text-neutral-700">
          Sent {lastResult.sent}, failed {lastResult.failed}, skipped {lastResult.skipped}.
          {lastResult.results.filter((r) => r.status === 'failed').map((r) => (
            <div key={payeeKey(r.recipient_kind, r.party_id)} className="text-xs text-red-600">
              {r.party_name}: {r.error}
            </div>
          ))}
        </div>
      )}

      {preview.groups.length === 0 ? (
        <p className="py-4 text-center text-sm text-neutral-400">No payees to email for this {scope.kind}.</p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-neutral-200">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
              <tr>
                <th className="w-8 px-3 py-2" />
                <th className="px-3 py-2">Payee</th>
                <th className="px-3 py-2">Email</th>
                <th className="px-3 py-2 text-right">Amount</th>
                <th className="px-3 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {preview.groups.map((g, i) => {
                const key = payeeKey(g.recipient_kind, g.party_id)
                const blocked = g.block_reasons.length > 0
                const status = readinessOf(g)
                return (
                  <tr key={key} className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40', blocked && 'text-neutral-400')}>
                    <td className="px-3 py-2">
                      <input type="checkbox" disabled={blocked} checked={selected.has(key)}
                        onChange={(e) => setSelected((prev) => {
                          const next = new Set(prev)
                          if (e.target.checked) next.add(key)
                          else next.delete(key)
                          return next
                        })} />
                    </td>
                    <td className="px-3 py-2">
                      {g.party_name}
                      <span className="ml-1 text-xs text-neutral-400">
                        ({g.recipient_kind === 'vendor' ? 'Vendor' : 'Employee'})
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs text-neutral-600">
                      {g.email || <span className="text-neutral-300">—</span>}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(g.total, g.currency)}</td>
                    <td className="px-3 py-2">
                      <RemittanceStatusBadge status={status} />
                      {blocked && <div className="mt-1 text-xs text-amber-700">{blockReasonText(g.block_reasons)}</div>}
                      {!blocked && g.last_send?.status === 'failed' && (
                        <div className="mt-1 text-xs text-red-600">{g.last_send.error}</div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex justify-end">
        <button type="button" disabled={sending || selectedCount === 0} onClick={() => void handleSend()} className={primaryBtn}>
          {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          {sending ? 'Sending…' : `Send (${selectedCount})`}
        </button>
      </div>
    </div>
  )
}
