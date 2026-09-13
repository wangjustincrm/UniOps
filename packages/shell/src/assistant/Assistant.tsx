/**
 * The assistant panel: a launcher button and a slide-over conversation.
 *
 * This component knows nothing about how answers are produced. The host app
 * passes `ask`, which is the only way out of here — so the shell package never
 * imports an app's API client, auth store, or base URL, and the same panel drops
 * into any of the seven front-ends.
 *
 * Two things it is opinionated about, both carried over from how the backend was
 * built:
 *
 *   - Every answer shows its evidence. A query the person can read, or the list
 *     of gates that were checked. An answer nobody can verify is worth little in
 *     a finance system, and the backend returns the receipt precisely so it can
 *     be shown.
 *   - A blocked document is split into what this person can fix and what they
 *     cannot. Sending someone to change a setting they have no access to is the
 *     failure this distinction exists to prevent, and burying it in prose would
 *     undo the work.
 *
 * It deliberately does not use the tab store: this is a global overlay, and
 * useTabStoreApi throws when there is no provider — which is exactly the case in
 * the iframe-embedded mode. The panel must not be the reason a page goes blank.
 */
import React from 'react'
import { createPortal } from 'react-dom'
import {
  AlertTriangle, Check, ChevronDown, Download, Loader2, MessageSquare, Send, X,
} from 'lucide-react'
import { cn } from '../lib/cn'
import type {
  AssistantCheck, AssistantContext, AssistantMessage, AssistantReply,
} from './types'

export interface AssistantProps {
  /**
   * Sends one question. Throw to signal a transport/HTTP failure.
   *
   * `history` is the conversation so far, oldest first. It is what lets "it" and
   * "that one" resolve — someone who just asked about a PO and then asks who it
   * is with will not repeat the number.
   */
  ask: (
    message: string,
    context: AssistantContext,
    history: { role: 'user' | 'assistant'; text: string }[]
  ) => Promise<AssistantReply>
  /**
   * Lists what this person can actually be answered about. Called once when the
   * panel first opens; failures are swallowed, since not knowing the coverage is
   * no reason to block the conversation.
   *
   * Worth wiring up: the ontology covers purchasing and nothing else yet, so in
   * an app about something else the honest thing is to say so up front rather
   * than let someone discover it one refused question at a time.
   */
  describeScope?: () => Promise<string[]>
  /**
   * Downloads the answer's query as a workbook. Shown only on answers that ran
   * one, and only when supplied — an app without it simply has no button.
   *
   * Deliberately a user action rather than something the model can trigger:
   * nothing gets written to a file until a person has read the answer and
   * decided it is right.
   */
  exportQuery?: (query: Record<string, unknown>, question: string) => Promise<void>
  /** Where the user currently is. Re-read on every send, so keep it current. */
  context?: AssistantContext
  /** Shown once, above the first message. */
  greeting?: string
  className?: string
}

const SUGGESTIONS = [
  'How many purchase orders do we have?',
  'Top 5 vendors by spend in the last 3 months',
  'Which invoices are still unmatched?',
]

let seq = 0
const nextId = () => `m${++seq}`

export function Assistant({ ask, describeScope, exportQuery, context, greeting, className }: AssistantProps) {
  const [open, setOpen] = React.useState(false)
  const [messages, setMessages] = React.useState<AssistantMessage[]>([])
  const [draft, setDraft] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [scope, setScope] = React.useState<string[] | null>(null)
  const listRef = React.useRef<HTMLDivElement>(null)
  const inputRef = React.useRef<HTMLTextAreaElement>(null)

  React.useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  React.useEffect(() => {
    if (!open || !describeScope || scope) return
    let live = true
    describeScope()
      .then((s) => { if (live) setScope(s) })
      .catch(() => { /* coverage is a nicety; never block on it */ })
    return () => { live = false }
  }, [open, describeScope, scope])

  React.useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, busy])

  // Escape closes, but only when nothing is in flight — losing an answer that
  // is already paid for is worse than an extra click.
  React.useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, busy])

  const send = React.useCallback(
    async (text: string) => {
      const question = text.trim()
      if (!question || busy) return
      setDraft('')
      // Snapshot before appending: the new question goes in `message`, not in
      // the history, or the model sees it twice.
      const priorTurns = messages
        .filter((m) => !m.error)
        .map((m) => ({ role: m.role, text: m.text }))
      setMessages((m) => [...m, { id: nextId(), role: 'user', text: question }])
      setBusy(true)
      try {
        const reply = await ask(question, context ?? {}, priorTurns)
        setMessages((m) => [
          ...m,
          { id: nextId(), role: 'assistant', text: reply.answer, reply },
        ])
      } catch (err) {
        setMessages((m) => [
          ...m,
          {
            id: nextId(),
            role: 'assistant',
            error: true,
            text:
              err instanceof Error && err.message
                ? err.message
                : 'The assistant is unavailable right now.',
          },
        ])
      } finally {
        setBusy(false)
      }
    },
    [ask, context, busy, messages]
  )

  const panel = (
    <div
      className="fixed inset-0 z-[60] flex justify-end"
      role="dialog"
      aria-modal="true"
      aria-label="Assistant"
    >
      <button
        type="button"
        aria-label="Close assistant"
        className="flex-1 bg-neutral-900/20"
        onClick={() => !busy && setOpen(false)}
      />
      <div className="flex h-full w-full max-w-[26rem] flex-col border-l border-neutral-200 bg-white shadow-xl">
        <header className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
          <div className="flex items-center gap-2">
            <MessageSquare className="h-4 w-4 text-primary-600" aria-hidden />
            <span className="text-sm font-semibold text-neutral-900">Assistant</span>
          </div>
          <button
            type="button"
            onClick={() => setOpen(false)}
            disabled={busy}
            aria-label="Close"
            className="rounded p-1 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-700 disabled:opacity-40"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </header>

        <div ref={listRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
          {messages.length === 0 && (
            <div className="space-y-3">
              <p className="text-sm text-neutral-600">
                {greeting ??
                  'Ask about purchasing data, or why a document is blocked. Answers come with the query or the checks behind them.'}
              </p>
              {scope && scope.length > 0 && (
                <p className="text-xs text-neutral-500">
                  I can currently answer about {scope.join(', ')}.
                </p>
              )}
              <div className="space-y-1.5">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => send(s)}
                    className="block w-full rounded-lg border border-neutral-200 px-3 py-2 text-left text-xs text-neutral-700 transition-colors hover:border-primary-600 hover:bg-primary-600/5"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <MessageRow
              key={m.id}
              message={m}
              exportQuery={exportQuery}
              // The question this answer came from, so the sheet can be headed
              // with it rather than with the entity name.
              question={i > 0 ? messages[i - 1].text : ''}
            />
          ))}

          {busy && (
            <div className="flex items-center gap-2 text-xs text-neutral-500">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              Thinking…
            </div>
          )}
        </div>

        <form
          className="border-t border-neutral-200 p-3"
          onSubmit={(e) => {
            e.preventDefault()
            send(draft)
          }}
        >
          <div className="flex items-end gap-2">
            <textarea
              ref={inputRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  send(draft)
                }
              }}
              rows={2}
              placeholder="Ask a question…"
              className="flex-1 resize-none rounded-lg border border-neutral-200 px-3 py-2 text-sm text-neutral-900 placeholder:text-neutral-400 focus-visible:border-primary-600 focus-visible:outline-none"
            />
            <button
              type="submit"
              disabled={busy || !draft.trim()}
              aria-label="Send"
              className="mb-0.5 rounded-lg bg-primary-600 p-2 text-white transition-colors hover:bg-primary-700 disabled:pointer-events-none disabled:opacity-40"
            >
              <Send className="h-4 w-4" aria-hidden />
            </button>
          </div>
        </form>
      </div>
    </div>
  )

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Open assistant"
        className={cn(
          'fixed bottom-6 right-6 z-50 flex h-12 w-12 items-center justify-center rounded-full bg-primary-600 text-white shadow-lg transition-colors hover:bg-primary-700 focus-visible:outline-none focus-visible:shadow-[0_0_0_3px_rgba(10,124,124,0.25)]',
          open && 'hidden',
          className
        )}
      >
        <MessageSquare className="h-5 w-5" aria-hidden />
      </button>
      {open && typeof document !== 'undefined' && createPortal(panel, document.body)}
    </>
  )
}

function MessageRow({
  message, exportQuery, question,
}: {
  message: AssistantMessage
  exportQuery?: AssistantProps['exportQuery']
  question?: string
}) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg rounded-br-sm bg-primary-600 px-3 py-2 text-sm text-white">
          {message.text}
        </div>
      </div>
    )
  }

  const reply = message.reply
  return (
    <div className="space-y-2">
      <div
        className={cn(
          'max-w-[92%] whitespace-pre-wrap rounded-lg rounded-bl-sm px-3 py-2 text-sm',
          message.error
            ? 'bg-danger-50 text-danger-700'
            : 'bg-neutral-100 text-neutral-800'
        )}
      >
        {message.text}
      </div>
      {reply?.preflight && <GateList preflight={reply.preflight} />}
      {reply?.workflow && <Chain workflow={reply.workflow} />}
      {reply?.query && exportQuery && (
        <ExportButton query={reply.query} question={question ?? ''} run={exportQuery} />
      )}
      {reply && <Evidence reply={reply} />}
    </div>
  )
}

function ExportButton({
  query, question, run,
}: {
  query: Record<string, unknown>
  question: string
  run: NonNullable<AssistantProps['exportQuery']>
}) {
  const [busy, setBusy] = React.useState(false)
  const [failed, setFailed] = React.useState(false)

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        disabled={busy}
        onClick={async () => {
          setBusy(true)
          setFailed(false)
          try {
            await run(query, question)
          } catch {
            setFailed(true)
          } finally {
            setBusy(false)
          }
        }}
        className="inline-flex items-center gap-1.5 rounded-lg border border-neutral-200 px-2.5 py-1 text-xs text-neutral-700 transition-colors hover:border-primary-600 hover:text-primary-600 disabled:opacity-50"
      >
        {busy ? (
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
        ) : (
          <Download className="h-3 w-3" aria-hidden />
        )}
        {busy ? 'Preparing…' : 'Export to Excel'}
      </button>
      {failed && (
        <span className="text-xs text-danger-600">Export failed — try again.</span>
      )}
    </div>
  )
}

/**
 * The gates, grouped by who can act on them.
 *
 * Splitting these two is the point of the whole preflight path: "add a vendor"
 * and "your department has no manager configured" are different sentences to the
 * person reading them, and only one of them is theirs to act on.
 */
function GateList({ preflight }: { preflight: NonNullable<AssistantReply['preflight']> }) {
  const failed = preflight.checks.filter((c) => !c.passed)
  if (failed.length === 0) return null
  const mine = failed.filter((c) => c.fixable_by_user)
  const theirs = failed.filter((c) => !c.fixable_by_user)

  return (
    <div className="space-y-2 rounded-lg border border-neutral-200 p-3">
      {!preflight.complete && (
        <p className="flex items-start gap-1.5 text-xs text-warning-700">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          The approval engine could not be reached, so this list may be incomplete.
        </p>
      )}
      {mine.length > 0 && <GateGroup title="You can fix" checks={mine} tone="fixable" />}
      {theirs.length > 0 && (
        <GateGroup title="Not yours to fix" checks={theirs} tone="blocked" />
      )}
    </div>
  )
}

function GateGroup({
  title, checks, tone,
}: { title: string; checks: AssistantCheck[]; tone: 'fixable' | 'blocked' }) {
  return (
    <div className="space-y-1.5">
      <p
        className={cn(
          'text-[11px] font-semibold uppercase tracking-wide',
          tone === 'fixable' ? 'text-primary-600' : 'text-danger-600'
        )}
      >
        {title}
      </p>
      <ul className="space-y-1.5">
        {checks.map((c) => (
          <li key={c.id} className="flex items-start gap-2 text-xs text-neutral-700">
            <span
              className={cn(
                'mt-0.5 shrink-0',
                tone === 'fixable' ? 'text-primary-600' : 'text-danger-600'
              )}
              aria-hidden
            >
              {tone === 'fixable' ? '•' : '!'}
            </span>
            <span className="min-w-0">
              {c.message ?? c.id}
              {c.owner && tone === 'blocked' && (
                <span className="ml-1 text-neutral-500">(ask {c.owner})</span>
              )}
              {c.fix_route && (
                <a
                  href={c.fix_route}
                  className="ml-1 break-all text-primary-600 underline underline-offset-2"
                >
                  open
                </a>
              )}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * Where the document sits and how it got there.
 *
 * Shown inline rather than behind the evidence toggle: "who is it with" is the
 * answer to the question, not the working behind it.
 */
function Chain({ workflow }: { workflow: NonNullable<AssistantReply['workflow']> }) {
  const { current_step: current, history, steps_available: complete } = workflow
  if (!current && history.length === 0) return null

  return (
    <div className="space-y-2 rounded-lg border border-neutral-200 p-3 text-xs">
      {!complete && (
        <p className="flex items-start gap-1.5 text-warning-700">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          The approval engine could not be reached, so the full chain is unknown —
          this is the recorded history only.
        </p>
      )}
      {current && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-primary-600">
            Waiting on
          </p>
          <p className="mt-0.5 text-neutral-800">
            {/* A broadcast step has no named holder; it sits with the role. */}
            {current.approver_name ?? `whoever holds ${current.label}`}
            <span className="ml-1 text-neutral-500">({current.label})</span>
          </p>
        </div>
      )}
      {history.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-neutral-500">
            History
          </p>
          <ul className="mt-0.5 space-y-1">
            {history.map((e, i) => (
              <li key={i} className="text-neutral-700">
                <span className="font-medium">{e.action ?? '—'}</span>
                {e.actor_name && <span> by {e.actor_name}</span>}
                {e.actor_role && <span className="text-neutral-500"> ({e.actor_role})</span>}
                {e.at && <span className="text-neutral-500"> · {String(e.at).slice(0, 10)}</span>}
                {e.comment && (
                  <span className="block pl-3 text-neutral-500">“{e.comment}”</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

/** Collapsed by default: available when someone wants to check, never in the way. */
function Evidence({ reply }: { reply: AssistantReply }) {
  const [open, setOpen] = React.useState(false)
  const s = reply.sources
  if (!reply.query && !s) return null
  // A workflow answer renders its chain inline; the toggle would only repeat it.
  if (reply.kind === 'workflow' && !reply.query) {
    return (
      <p className="text-xs text-neutral-500">
        {s?.document} · {s?.steps ?? 0} steps, {s?.events ?? 0} events
        {s?.complete === false && ' · chain incomplete'}
      </p>
    )
  }

  // Each kind of answer has different working behind it. Describing a workflow
  // reply as "query — 0 rows" was not just unhelpful, it was untrue: no query
  // ran, and zero rows implied a search that had found nothing.
  const summary =
    reply.kind === 'preflight'
      ? `${s?.document ?? 'document'} — ${reply.preflight?.checks.length ?? 0} checks`
      : reply.kind === 'workflow'
        ? `${s?.document ?? 'document'} — ${s?.steps ?? 0} step${s?.steps === 1 ? '' : 's'}, ${
            s?.events ?? 0
          } event${s?.events === 1 ? '' : 's'}`
        : `${s?.entity ?? 'query'} — ${s?.row_count ?? 0} row${s?.row_count === 1 ? '' : 's'}${
            s?.truncated ? ' (first page)' : ''
          }`

  return (
    <div className="text-xs">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-neutral-500 hover:text-neutral-700"
      >
        <ChevronDown
          className={cn('h-3 w-3 transition-transform', open && 'rotate-180')}
          aria-hidden
        />
        {open ? 'Hide' : 'Show'} evidence · {summary}
      </button>
      {open && (
        <div className="mt-1.5 space-y-1.5">
          {reply.query && (
            <pre className="overflow-x-auto rounded border border-neutral-200 bg-neutral-50 p-2 text-[11px] leading-relaxed text-neutral-700">
              {JSON.stringify(reply.query, null, 2)}
            </pre>
          )}
          {reply.preflight && (
            <ul className="space-y-0.5">
              {reply.preflight.checks.map((c) => (
                <li key={c.id} className="flex items-center gap-1.5 text-neutral-600">
                  {c.passed ? (
                    <Check className="h-3 w-3 shrink-0 text-success-600" aria-hidden />
                  ) : (
                    <X className="h-3 w-3 shrink-0 text-danger-600" aria-hidden />
                  )}
                  <span className="font-mono text-[11px]">{c.id}</span>
                  <span className="text-neutral-400">{c.layer}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
