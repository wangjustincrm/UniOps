/**
 * Sign-off panel for NC-imported POs.
 *
 * Replaces what used to happen on paper: print the PO, get the Purchasing
 * Manager's initials and the OPM's signature, then release the order in NC.
 * The signatures come from each signer's My Profile and are stamped onto the
 * PO PDF.
 *
 * Sign-off state is independent of the PO's own status — nc_purchase_sync
 * rewrites that from NC on every run — so nothing here reads or moves it.
 */
import { useState } from 'react'
import {
  PenLine, Check, Undo2, MessageSquarePlus, Loader2, AlertTriangle, Clock,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatDateTime } from '@/lib/utils'
import {
  usePoSignoff, useSubmitPoSignoff, useSignPoSignoff, useReturnPoSignoff,
  useAddPoSignoffNote,
} from '@/hooks/usePos'
import type { PoSignoffState, PoSignoffStatus, PoSignoffStep } from '@/services/po'

export const STATUS_LABELS: Record<PoSignoffStatus, { label: string; className: string }> = {
  draft:      { label: 'Not started',       className: 'bg-neutral-100 text-neutral-600' },
  submitted:  { label: 'Awaiting signature', className: 'bg-warning-100 text-warning-700' },
  in_review:  { label: 'Awaiting signature', className: 'bg-warning-100 text-warning-700' },
  approved:   { label: 'Signed',            className: 'bg-success-100 text-success-700' },
  returned:   { label: 'Returned',          className: 'bg-warning-100 text-warning-700' },
  rejected:   { label: 'Rejected',          className: 'bg-danger-100 text-danger-700' },
  cancelled:  { label: 'Cancelled',         className: 'bg-neutral-100 text-neutral-500' },
}

export const SLOT_LABELS: Record<string, string> = {
  initials:  'Initials on PDF',
  signature: 'Signature on PDF',
}

export const THREAD_LABELS: Record<string, string> = {
  submit:  'Raised the sign-off',
  approve: 'Signed',
  return:  'Returned',
  reject:  'Rejected',
  note:    'Added a note',
  cancel:  'Cancelled',
}

/** Open composer, or null when none is. Only one can be open at a time. */
type Composer = 'submit' | 'sign' | 'return' | 'note' | null

function StepRow({ step, index, state }: {
  step: PoSignoffStep
  index: number
  state: PoSignoffState
}) {
  const isOpen = state.status === 'submitted' || state.status === 'in_review'
  const current = isOpen && index === state.step_idx
  const signed = step.signed_at !== null

  return (
    <div className={cn(
      'flex items-center gap-3 rounded-xl border px-3 py-2.5',
      current ? 'border-warning-200 bg-warning-50' : 'border-neutral-200 bg-white',
    )}>
      <span className={cn(
        'flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold',
        signed ? 'bg-success-100 text-success-700'
               : current ? 'bg-warning-100 text-warning-700'
                         : 'bg-neutral-100 text-neutral-500',
      )}>
        {signed ? <Check className="h-3.5 w-3.5" /> : index + 1}
      </span>

      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-neutral-800">{step.label}</p>
        <p className="truncate text-xs text-neutral-500">
          {signed
            ? `Signed by ${step.signed_by_name} · ${formatDateTime(step.signed_at)}`
            : current ? 'Waiting for signature'
                      : step.holder_count === 0 ? 'No active holder'
                                                : 'Not reached yet'}
        </p>
      </div>

      <span className="shrink-0 rounded-md bg-neutral-100 px-2 py-0.5 text-[11px] font-medium text-neutral-500">
        {step.sig_slot ? SLOT_LABELS[step.sig_slot] : 'Not on PDF'}
      </span>
    </div>
  )
}

function Composer({ title, placeholder, required, confirmLabel, isPending, onCancel, onConfirm }: {
  title: string
  placeholder: string
  required: boolean
  confirmLabel: string
  isPending: boolean
  onCancel: () => void
  onConfirm: (text: string) => void
}) {
  const [text, setText] = useState('')
  const blocked = isPending || (required && text.trim() === '')

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-neutral-200 bg-neutral-50 p-3">
      <p className="text-xs font-semibold text-neutral-700">{title}</p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={4}
        placeholder={placeholder}
        className="w-full rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm focus:border-primary-500 focus:outline-none"
      />
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          disabled={blocked}
          // Guarded twice: disabled stops the obvious double-click, and the
          // re-check inside stops the one that lands before React re-renders.
          onClick={() => { if (blocked) return; onConfirm(text.trim()) }}
          className="gap-1.5"
        >
          {isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {confirmLabel}
        </Button>
        <button
          type="button"
          onClick={onCancel}
          disabled={isPending}
          className="text-xs font-medium text-neutral-500 hover:text-neutral-700 disabled:opacity-40"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

export function PoSignoffPanel({ poId, source }: { poId: string; source: string | null }) {
  // Only NC imports run this flow; a locally raised PO walks the PO approval
  // chain instead. Asking the endpoint for anything else would just 409.
  const enabled = source === 'nc'
  const { data: state, isLoading, error } = usePoSignoff(poId, enabled)
  const [composer, setComposer] = useState<Composer>(null)
  const [failure, setFailure] = useState('')

  const submit = useSubmitPoSignoff(poId)
  const sign = useSignPoSignoff(poId)
  const back = useReturnPoSignoff(poId)
  const note = useAddPoSignoffNote(poId)
  const pending = submit.isPending || sign.isPending || back.isPending || note.isPending

  if (!enabled) return null
  if (isLoading) {
    return (
      <div className="rounded-lg border border-neutral-200 bg-white px-6 py-5">
        <Loader2 className="h-4 w-4 animate-spin text-neutral-400" />
      </div>
    )
  }
  // A signatory who is not part of this sign-off (and has no scope on the PO)
  // gets a 404 here — no panel rather than an error box.
  if (error || !state) return null

  const run = (fn: () => Promise<unknown>) => {
    if (pending) return
    setFailure('')
    fn().then(() => setComposer(null))
       .catch((e: unknown) => setFailure(e instanceof Error ? e.message : 'Action failed'))
  }

  const status = STATUS_LABELS[state.status] ?? STATUS_LABELS.draft
  const isOpen = state.status === 'submitted' || state.status === 'in_review'
  const canRestart = state.status === 'draft' || state.status === 'returned'

  return (
    <div className="rounded-lg border border-neutral-200 bg-white">
      <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-50 text-primary-600">
            <PenLine className="h-4 w-4" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-neutral-900">Sign-off</h2>
            {state.submitted_by_name && (
              <p className="text-xs text-neutral-500">
                Raised by {state.submitted_by_name}
                {state.submitted_at ? ` · ${formatDateTime(state.submitted_at)}` : ''}
              </p>
            )}
          </div>
        </div>
        <span className={cn('rounded-md px-2.5 py-1 text-xs font-semibold', status.className)}>
          {status.label}
        </span>
      </div>

      <div className="flex flex-col gap-4 px-6 py-5">
        <div className="flex flex-col gap-2">
          {state.steps.map((step, i) => (
            <StepRow key={step.id} step={step} index={i} state={state} />
          ))}
        </div>

        {canRestart && state.blockers.length > 0 && (
          <div className="flex flex-col gap-1.5 rounded-xl border border-warning-200 bg-warning-50 px-4 py-3">
            {state.blockers.map((blocker) => (
              <p key={blocker} className="flex items-start gap-2 text-xs text-warning-700">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                {blocker}
              </p>
            ))}
          </div>
        )}

        {state.thread.length > 0 && (
          <div className="flex flex-col gap-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-neutral-400">
              Justification &amp; notes
            </p>
            <div className="flex flex-col gap-2">
              {state.thread.map((entry, i) => (
                <div key={`${entry.at}-${i}`} className="rounded-xl border border-neutral-100 bg-neutral-50 px-3 py-2">
                  <p className="flex items-center gap-1.5 text-xs text-neutral-500">
                    <Clock className="h-3 w-3 shrink-0" />
                    <span className="font-medium text-neutral-700">
                      {entry.actor_name ?? 'Unknown'}
                    </span>
                    · {THREAD_LABELS[entry.action] ?? entry.action}
                    · {formatDateTime(entry.at)}
                  </p>
                  {entry.comment && (
                    <p className="mt-1 whitespace-pre-wrap text-sm text-neutral-800">{entry.comment}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {failure && (
          <p className="flex items-start gap-1.5 text-xs text-danger-600">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {failure}
          </p>
        )}

        {composer === 'submit' && (
          <Composer
            title="Why is this being bought?"
            placeholder="What it is for, why this quantity, anything the signatories need to judge it."
            required
            confirmLabel="Submit for sign-off"
            isPending={submit.isPending}
            onCancel={() => setComposer(null)}
            onConfirm={(text) => run(() => submit.mutateAsync(text))}
          />
        )}
        {composer === 'sign' && (
          <Composer
            title="Sign this PO"
            placeholder="Optional note to record with your signature."
            required={false}
            confirmLabel="Sign"
            isPending={sign.isPending}
            onCancel={() => setComposer(null)}
            onConfirm={(text) => run(() => sign.mutateAsync(text || undefined))}
          />
        )}
        {composer === 'return' && (
          <Composer
            title="Return for revision"
            placeholder="What needs explaining or changing before you can sign."
            required
            confirmLabel="Return"
            isPending={back.isPending}
            onCancel={() => setComposer(null)}
            onConfirm={(text) => run(() => back.mutateAsync(text))}
          />
        )}
        {composer === 'note' && (
          <Composer
            title="Add a note"
            placeholder="Ask for detail, or add it — the thread is kept with the sign-off."
            required
            confirmLabel="Add note"
            isPending={note.isPending}
            onCancel={() => setComposer(null)}
            onConfirm={(text) => run(() => note.mutateAsync(text))}
          />
        )}

        {composer === null && (
          <div className="flex flex-wrap items-center gap-2">
            {state.can_submit && (
              <Button size="sm" disabled={pending} onClick={() => setComposer('submit')} className="gap-1.5">
                <PenLine className="h-3.5 w-3.5" />
                {state.status === 'returned' ? 'Resubmit for sign-off' : 'Submit for sign-off'}
              </Button>
            )}
            {state.can_sign && (
              <>
                <Button size="sm" disabled={pending} onClick={() => setComposer('sign')} className="gap-1.5">
                  <Check className="h-3.5 w-3.5" /> Sign
                </Button>
                <Button
                  size="sm" variant="secondary" disabled={pending}
                  onClick={() => setComposer('return')} className="gap-1.5"
                >
                  <Undo2 className="h-3.5 w-3.5" /> Return
                </Button>
              </>
            )}
            {state.can_note && isOpen && (
              <button
                type="button"
                disabled={pending}
                onClick={() => setComposer('note')}
                className="flex items-center gap-1.5 text-xs font-medium text-neutral-500 hover:text-primary-600 disabled:opacity-40"
              >
                <MessageSquarePlus className="h-3.5 w-3.5" /> Add note
              </button>
            )}
          </div>
        )}

        {state.status === 'approved' && (
          <p className="rounded-xl border border-success-200 bg-success-50 px-4 py-3 text-xs text-success-700">
            Every signatory has signed and the PO PDF now carries their
            signatures. The order can be released in NC.
          </p>
        )}
      </div>
    </div>
  )
}
