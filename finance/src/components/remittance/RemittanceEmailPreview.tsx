/**
 * Confirmation step of RemittancePanel: the emails exactly as they will be
 * sent (From / To / Cc / Subject / body), rendered by the server's
 * `.../remittance/render` endpoint — the same code path as the real send, so
 * what AP reviews here is what the payee receives. Nothing has been sent
 * while this is on screen; only the confirm button sends.
 *
 * One email at a time with a payee switcher rather than every body stacked:
 * a batch can cover dozens of payees, and each body is a full HTML document.
 */
import { useState } from 'react'
import { ArrowLeft, ChevronLeft, ChevronRight, Loader2, Send } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { RenderedEmail } from '@/services/remittance'
import { primaryBtn, secondaryBtn } from './buttonStyles'

/**
 * The email body in a sandboxed iframe: its styles cannot leak into the page
 * and vice versa, and with no `allow-scripts` nothing in it can run.
 * `allow-same-origin` is only there so onLoad can read the content height —
 * without scripts it grants the document nothing.
 */
function EmailBody({ html }: { html: string }) {
  const [height, setHeight] = useState(360)
  return (
    <iframe
      title="Email body"
      sandbox="allow-same-origin"
      srcDoc={html}
      style={{ height }}
      className="w-full rounded-md border border-neutral-200 bg-white"
      onLoad={(e) => {
        const doc = e.currentTarget.contentDocument
        if (doc) setHeight(Math.min(Math.max(doc.documentElement.scrollHeight + 16, 200), 800))
      }}
    />
  )
}

function HeaderRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <span className="w-16 shrink-0 text-neutral-400">{label}</span>
      <span className="break-all text-neutral-700">{value}</span>
    </div>
  )
}

export function RemittanceEmailPreview({ emails, sending, onBack, onConfirm }: {
  emails: RenderedEmail[]
  sending: boolean
  onBack: () => void
  onConfirm: () => void
}) {
  const [index, setIndex] = useState(0)
  const readyCount = emails.filter((e) => e.status === 'ready').length
  const notSent = emails.filter((e) => e.status !== 'ready')
  const current = emails[Math.min(index, emails.length - 1)]

  return (
    <div className="space-y-3">
      <div className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
        Nothing has been sent yet. Review {readyCount === 1 ? 'the email' : `the ${readyCount} emails`} below,
        then click Confirm &amp; Send.
      </div>

      {notSent.length > 0 && (
        <div className="rounded-md bg-neutral-50 px-3 py-2 text-sm text-neutral-600">
          Will not be sent:
          {notSent.map((e) => (
            <div key={`${e.recipient_kind}:${e.party_id}`} className="text-xs">
              {e.party_name} — {e.error}
            </div>
          ))}
        </div>
      )}

      {emails.length > 1 && (
        <div className="flex items-center gap-2">
          <button type="button" aria-label="Previous email" disabled={index === 0}
            onClick={() => setIndex((i) => i - 1)} className={secondaryBtn}>
            <ChevronLeft className="h-4 w-4" />
          </button>
          <select value={index} onChange={(e) => setIndex(Number(e.target.value))}
            className="min-w-0 flex-1 rounded-md border border-neutral-300 px-2 py-1.5 text-sm">
            {emails.map((e, i) => (
              <option key={`${e.recipient_kind}:${e.party_id}`} value={i}>
                {i + 1} / {emails.length} — {e.party_name}{e.status !== 'ready' ? ' (not sent)' : ''}
              </option>
            ))}
          </select>
          <button type="button" aria-label="Next email" disabled={index >= emails.length - 1}
            onClick={() => setIndex((i) => i + 1)} className={secondaryBtn}>
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      )}

      {current && (current.status === 'ready' ? (
        <div className="space-y-2">
          <div className="space-y-0.5 rounded-md border border-neutral-200 px-3 py-2 text-sm">
            <HeaderRow label="From" value={current.from} />
            <HeaderRow label="To" value={current.to} />
            {current.cc && <HeaderRow label="Cc" value={current.cc} />}
            <HeaderRow label="Subject" value={current.subject} />
          </div>
          <EmailBody key={`${current.recipient_kind}:${current.party_id}`} html={current.html} />
        </div>
      ) : (
        <p className={cn('rounded-md px-3 py-4 text-center text-sm',
          current.status === 'failed' ? 'bg-red-50 text-red-700' : 'bg-neutral-50 text-neutral-500')}>
          {current.party_name} will not be emailed: {current.error}
        </p>
      ))}

      <div className="flex justify-end gap-2">
        <button type="button" onClick={onBack} disabled={sending} className={secondaryBtn}>
          <ArrowLeft className="h-4 w-4" />
          Back
        </button>
        <button type="button" onClick={onConfirm} disabled={sending || readyCount === 0} className={primaryBtn}>
          {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          {sending ? 'Sending…' : `Confirm & Send (${readyCount})`}
        </button>
      </div>
    </div>
  )
}
