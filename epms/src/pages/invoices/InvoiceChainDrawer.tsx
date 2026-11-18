/**
 * The document chain behind one invoice, shown when its Due Date cell is
 * clicked in the Invoice List.
 *
 * Every state here comes from GET /invoices/{id}/chain — nothing is derived
 * locally. The PA and Payment steps in particular CANNOT be answered from the
 * list row: the invoice→PA link lives in payment_applications.invoice_ids, a
 * JSONB array with no foreign key, so only the server can resolve it.
 */
import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  Ban, Check, CircleDashed, EyeOff, Loader2, Minus, X,
} from 'lucide-react'

import { useInvoiceChain } from '@/hooks/useInvoices'
import { cn, formatDate } from '@/lib/utils'
import { dueInfo, type DueTone } from '@/lib/dueDate'
import type { ChainStep, ChainStepKey, ChainStepRef, ChainStepState } from '@/services/invoices'

import { Button } from '@uniops/shell'
const STEP_TITLES: Record<ChainStepKey, string> = {
  match_po: 'Match PO',
  link_gr: 'Link GR',
  create_pa: 'Create PA',
  payment: 'Payment',
}

/** What the reader should do about this step, in plain words. */
const STEP_CAPTIONS: Record<ChainStepKey, Partial<Record<ChainStepState, string>>> = {
  match_po: {
    pending: 'Not matched to a PO or agreement yet',
    blocked: 'Not matched yet',
  },
  link_gr: {
    pending: 'No goods receipt linked yet',
    blocked: 'Waiting for the invoice to be matched',
    not_applicable: 'Agreement route — settled against receipts, not GRs',
  },
  create_pa: {
    pending: 'Ready — no payment application raised yet',
    blocked: 'Waiting for the match and receipt to be in place',
    restricted: 'You do not have access to payment applications',
  },
  payment: {
    pending: 'Approved — waiting for the payment run',
    blocked: 'Waiting for a payment application to be approved',
    restricted: 'You do not have access to payment applications',
  },
}

const STATE_STYLES: Record<ChainStepState, { ring: string; text: string }> = {
  done:           { ring: 'bg-success-solid text-white',            text: 'text-fg' },
  pending:        { ring: 'bg-warning-solid text-white',            text: 'text-fg' },
  blocked:        { ring: 'bg-surface-sunken text-fg-muted',      text: 'text-fg-muted' },
  not_applicable: { ring: 'bg-surface-sunken text-fg-muted',      text: 'text-fg-muted' },
  restricted:     { ring: 'bg-surface-sunken text-fg-muted',      text: 'text-fg-muted' },
}

function StateIcon({ state }: { state: ChainStepState }) {
  const cls = 'h-3.5 w-3.5'
  if (state === 'done') return <Check className={cls} />
  if (state === 'pending') return <CircleDashed className={cls} />
  if (state === 'not_applicable') return <Minus className={cls} />
  if (state === 'restricted') return <EyeOff className={cls} />
  return <Ban className={cls} />
}

const REF_PATHS: Record<ChainStepRef['doc_type'], string> = {
  po: '/po',
  agreement: '/agreements',
  gr: '/gr',
  pa: '/pa',
}

function RefLink({ refItem }: { refItem: ChainStepRef }) {
  return (
    <Link
      to={`${REF_PATHS[refItem.doc_type]}/${refItem.id}`}
      className="font-mono text-xs text-primary-600 hover:underline"
    >
      {refItem.number ?? refItem.id.slice(0, 8)}
    </Link>
  )
}

function StepRow({ step, last }: { step: ChainStep; last: boolean }) {
  const style = STATE_STYLES[step.state]
  // A step with no document to link may say why itself (the Direct PA route:
  // matched to nothing, no goods receipt) — that wins over the generic caption.
  const caption = step.refs.length === 0 && step.detail && step.key !== 'payment'
    ? step.detail
    : STEP_CAPTIONS[step.key][step.state]

  return (
    <li className="flex gap-3">
      <div className="flex flex-col items-center">
        <span className={cn('flex h-6 w-6 items-center justify-center rounded-full', style.ring)}>
          <StateIcon state={step.state} />
        </span>
        {!last && <span className="w-px flex-1 bg-surface-strong" aria-hidden />}
      </div>

      <div className={cn('flex-1', last ? 'pb-0' : 'pb-5')}>
        <p className={cn('text-sm font-semibold', style.text)}>{STEP_TITLES[step.key]}</p>

        {step.refs.length > 0 && (
          <div className="mt-1 flex flex-wrap items-center gap-2">
            {step.refs.map((r) => <RefLink key={r.id} refItem={r} />)}
            {/* For a PA this is its approval status; for Payment, when it was paid. */}
            {step.detail && (
              <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-2xs capitalize text-fg-secondary">
                {step.key === 'payment' ? formatDate(step.detail) : step.detail.replace(/_/g, ' ')}
              </span>
            )}
          </div>
        )}

        {step.refs.length === 0 && step.detail && step.key === 'payment' && (
          <p className="mt-1 text-xs text-fg-secondary">Paid {formatDate(step.detail)}</p>
        )}

        {caption && <p className="mt-1 text-xs text-fg-muted">{caption}</p>}
      </div>
    </li>
  )
}

const DUE_HEADLINE: Record<DueTone, string> = {
  overdue: 'text-danger-fg',
  soon: 'text-warning-fg',
  normal: 'text-fg-secondary',
  settled: 'text-fg-muted',
}

export function InvoiceChainDrawer({
  invoiceId,
  onClose,
}: {
  invoiceId: string
  onClose: () => void
}) {
  const { data, isLoading, isError } = useInvoiceChain(invoiceId)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const due = data ? dueInfo(data.due_date, data.status) : null

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/40"
      onClick={onClose}
      role="presentation"
    >
      <aside
        className="flex h-full w-full max-w-md flex-col bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Invoice status"
      >
        <header className="flex items-start justify-between border-b border-line px-5 py-4">
          <div>
            <p className="text-xs font-semibold text-fg-muted">
              Invoice status
            </p>
            <p className="mt-0.5 font-mono text-sm text-fg">
              {data?.internal_ref ?? '—'}
            </p>
            {data && due && (
              <p className="mt-1 flex items-center gap-2 text-xs">
                <span className="text-fg-muted">Due {formatDate(data.due_date)}</span>
                {due.label && (
                  <span className={cn('font-semibold', DUE_HEADLINE[due.tone])}>{due.label}</span>
                )}
              </p>
            )}
          </div>
          <Button variant="subtle" size="icon-xs"
            onClick={onClose}
           
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </Button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-5">
          {isLoading && (
            <div className="flex items-center gap-2 text-sm text-fg-muted">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading…
            </div>
          )}
          {isError && (
            <p className="text-sm text-danger-fg">Could not load this invoice's status.</p>
          )}
          {data && (
            <ol>
              {data.steps.map((s, i) => (
                <StepRow key={s.key} step={s} last={i === data.steps.length - 1} />
              ))}
            </ol>
          )}
        </div>
      </aside>
    </div>
  )
}
