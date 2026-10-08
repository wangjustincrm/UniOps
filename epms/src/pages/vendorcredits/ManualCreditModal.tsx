import { useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Mail, Paperclip, Search, X } from 'lucide-react'

import { useCreateVendorCredit } from '@/hooks/useVendorCredits'
import { useVendors } from '@/hooks/useVendors'
import { cn, formatAmount } from '@/lib/utils'
import {
  CREDIT_EVIDENCE_ACCEPT, creditAttachmentService, type VendorCredit,
} from '@/services/vendorCredits'

/**
 * Record a vendor credit that has no credit-note document behind it.
 *
 * Some vendors refuse to issue a credit memo — typically for a duplicate
 * payment they tell AP by email to "use invoice #…REV -2,216.15 on your next
 * payment". The credit is just as real, so it goes into the same ledger, the
 * same pending_review queue and the same FIFO netting as an uploaded credit
 * note. What differs is the evidence: the vendor's email (saved as .msg/.eml,
 * or printed to PDF) is attached instead, and the notes must say who confirmed
 * it — finance-api refuses a manual credit without them.
 */

const CURRENCIES = ['CAD', 'USD', 'EUR', 'RMB']

// Not toISOString(): that is the UTC date, which is tomorrow after ~20:00 here.
const localToday = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

type Vendor = { id: string; name: string; code: string }

interface Props {
  onClose: () => void
  onCreated: (credit: VendorCredit) => void
}

export function ManualCreditModal({ onClose, onCreated }: Props) {
  const [vendor, setVendor] = useState<Vendor | null>(null)
  const [vendorQuery, setVendorQuery] = useState('')
  const [vendorOpen, setVendorOpen] = useState(false)
  const { data: vendorsData } = useVendors({ search: vendorQuery || undefined, active_only: true, page_size: 50 })
  const vendorOptions = vendorsData?.items ?? []

  const [reference, setReference] = useState('')
  const [creditDate, setCreditDate] = useState(localToday())
  const [currency, setCurrency] = useState('CAD')
  const [amount, setAmount] = useState('')
  const [tax, setTax] = useState('')
  const [poNumber, setPoNumber] = useState('')
  const [notes, setNotes] = useState('')
  const [files, setFiles] = useState<File[]>([])

  const [submitted, setSubmitted] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Set once the credit exists but some evidence failed to attach — the credit
  // is not rolled back (it is harmless while pending_review), the operator is
  // told exactly which files to add again from the detail panel.
  const [partial, setPartial] = useState<{ credit: VendorCredit; failed: string[] } | null>(null)

  const create = useCreateVendorCredit()

  // Vendors quote the figure either way round ("-2,216.15"); the ledger stores
  // positives and finance-api normalises too, so accept both here.
  const amt = Math.abs(Number(amount) || 0)
  const taxAmt = Math.abs(Number(tax) || 0)
  const total = amt + taxAmt

  const missing = {
    vendor: !vendor,
    reference: !reference.trim(),
    date: !creditDate,
    amount: amt <= 0,
    notes: !notes.trim(),
    files: files.length === 0,
  }
  const valid = !Object.values(missing).some(Boolean)

  const addFiles = (list: FileList | null) => {
    if (!list) return
    const incoming = Array.from(list)
    setFiles((prev) => [...prev, ...incoming.filter((f) => !prev.some((p) => p.name === f.name && p.size === f.size))])
  }

  const submit = async () => {
    setSubmitted(true)
    setError(null)
    if (!valid || !vendor || busy) return
    setBusy(true)
    let credit: VendorCredit
    try {
      credit = await create.mutateAsync({
        vendor_id: vendor.id,
        vendor_name: vendor.name,
        vendor_credit_number: reference.trim(),
        credit_date: creditDate,
        currency,
        amount: amt,
        tax_amount: taxAmt,
        po_number: poNumber.trim() || null,
        file_name: files[0]?.name ?? null,
        notes: notes.trim(),
        source: 'manual',
      })
    } catch (err) {
      const status = (err as { status?: number }).status
      setError(status === 409
        ? `${(err as Error).message}. It is already in Vendor Credits — check the existing entry instead of recording it again.`
        : err instanceof Error ? err.message : 'Could not record the credit')
      setBusy(false)
      return
    }
    const failed: string[] = []
    for (const f of files) {
      try { await creditAttachmentService.upload(credit.id, f) }
      catch (e) { failed.push(e instanceof Error ? e.message : f.name) }
    }
    setBusy(false)
    if (failed.length) { setPartial({ credit, failed }); return }
    onCreated(credit)
  }

  const input = (bad: boolean) => cn(
    'h-9 w-full rounded-lg border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
    submitted && bad ? 'border-danger-400' : 'border-neutral-300',
  )

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
      <div className="flex max-h-[92vh] w-full max-w-2xl flex-col rounded-2xl bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary-50">
              <Mail className="h-5 w-5 text-primary-600" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-neutral-900">Record Manual Credit</h2>
              <p className="text-xs text-neutral-400">
                For a credit the vendor confirmed in writing but will not issue a credit note for
              </p>
            </div>
          </div>
          <button onClick={onClose} disabled={busy} aria-label="Close"
                  className="text-neutral-400 hover:text-neutral-600 disabled:opacity-50">
            <X className="h-5 w-5" />
          </button>
        </div>

        {partial ? (
          <div className="flex flex-col gap-3 px-6 py-5 text-sm">
            <div className="flex items-start gap-2 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-warning-800">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <p>
                  <span className="font-mono font-semibold">{partial.credit.credit_number}</span> was recorded,
                  but {partial.failed.length === 1 ? 'a file' : `${partial.failed.length} files`} did not attach:
                </p>
                <ul className="mt-1 list-disc pl-5 text-xs">
                  {partial.failed.map((f) => <li key={f}>{f}</li>)}
                </ul>
                <p className="mt-2 text-xs">
                  Open the credit from the Pending Review list and add the file again — it cannot be approved without its evidence.
                </p>
              </div>
            </div>
            <div className="flex justify-end">
              <button className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white"
                      onClick={() => onCreated(partial.credit)}>
                OK
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-4 overflow-y-auto px-6 py-5">
              <div className="grid grid-cols-2 gap-3">
                <div className="relative flex flex-col gap-1">
                  <label className="text-xs font-medium text-neutral-700">
                    Vendor <span className="ml-0.5 text-danger-600">*</span>
                  </label>
                  <div className="relative">
                    <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-neutral-400" />
                    <input
                      type="text" placeholder="Search vendor by name or code…"
                      value={vendor ? vendor.name : vendorQuery}
                      onFocus={() => { setVendorOpen(true); if (vendor) setVendorQuery('') }}
                      onChange={(e) => { setVendorQuery(e.target.value); setVendor(null); setVendorOpen(true) }}
                      className={cn(input(missing.vendor), 'pl-8 pr-8')}
                    />
                    {vendor && (
                      <button type="button" aria-label="Clear vendor"
                              onClick={() => { setVendor(null); setVendorQuery('') }}
                              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600">
                        <X className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                  {submitted && missing.vendor && <p className="text-xs text-danger-600">Select a vendor</p>}
                  {vendorOpen && !vendor && (
                    <>
                      <div className="fixed inset-0 z-10" onClick={() => setVendorOpen(false)} />
                      <div className="absolute left-0 top-full z-20 mt-1 max-h-44 w-full overflow-y-auto rounded-lg border border-neutral-200 bg-white py-1 shadow-lg">
                        {vendorOptions.map((v) => (
                          <button key={v.id} type="button"
                                  onClick={() => { setVendor({ id: v.id, name: v.name, code: v.code }); setVendorOpen(false); setVendorQuery('') }}
                                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-primary-50">
                            <span className="rounded bg-neutral-100 px-1.5 py-0.5 font-mono text-xs text-neutral-600">{v.code}</span>
                            {v.name}
                          </button>
                        ))}
                        {vendorOptions.length === 0 && <p className="px-3 py-2 text-xs text-neutral-400">No vendors found</p>}
                      </div>
                    </>
                  )}
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-neutral-700">
                    Vendor Reference # <span className="ml-0.5 text-danger-600">*</span>
                  </label>
                  <input value={reference} onChange={(e) => setReference(e.target.value)}
                         placeholder="e.g. 8480321003REV" className={cn(input(missing.reference), 'font-mono')} />
                  <p className="text-[11px] text-neutral-400">
                    The number the vendor asked you to quote. It goes on the remittance, and blocks recording the same credit twice.
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-4 gap-3">
                <div className="col-span-2 flex flex-col gap-1">
                  <label className="text-xs font-medium text-neutral-700">
                    Credit Date <span className="ml-0.5 text-danger-600">*</span>
                  </label>
                  <input type="date" value={creditDate} onChange={(e) => setCreditDate(e.target.value)}
                         className={input(missing.date)} />
                  <p className="text-[11px] text-neutral-400">Usually the date of the vendor's email.</p>
                </div>
                <div className="col-span-2 flex flex-col gap-1">
                  <label className="text-xs font-medium text-neutral-700">Currency</label>
                  <select value={currency} onChange={(e) => setCurrency(e.target.value)} className={input(false)}>
                    {CURRENCIES.map((c) => <option key={c}>{c}</option>)}
                  </select>
                  <p className="text-[11px] text-neutral-400">Only nets off payments in the same currency.</p>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-neutral-700">
                    Amount (pre-tax) <span className="ml-0.5 text-danger-600">*</span>
                  </label>
                  <input type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)}
                         placeholder="2216.15" className={cn(input(missing.amount), 'font-mono')} />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-neutral-700">Tax</label>
                  <input type="number" step="0.01" value={tax} onChange={(e) => setTax(e.target.value)}
                         placeholder="0.00" className={cn(input(false), 'font-mono')} />
                </div>
                <div className="flex flex-col gap-1">
                  <span className="text-xs font-medium text-neutral-700">Credit Total</span>
                  <span className="flex h-9 items-center font-mono font-semibold text-neutral-900">
                    {formatAmount(total, currency)}
                  </span>
                </div>
              </div>
              <p className="-mt-2 text-[11px] text-neutral-400">
                Enter the figure as the vendor quoted it — a minus sign is fine, credits are always stored as positive.
                If the vendor gave one all-in figure, put it all in Amount.
              </p>

              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-neutral-700">PO # (optional)</label>
                <input value={poNumber} onChange={(e) => setPoNumber(e.target.value)}
                       className={cn(input(false), 'font-mono')} />
              </div>

              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-neutral-700">
                  Basis <span className="ml-0.5 text-danger-600">*</span>
                </label>
                <textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)}
                          placeholder="Why this credit exists and who confirmed it — e.g. Duplicate payment of invoice 8480321003. Hema Nanjiani (MSC collections) confirmed by email on 2026-09-25 that no credit memo will be issued; deduct on next payment."
                          className={cn(
                            'w-full rounded-lg border p-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
                            submitted && missing.notes ? 'border-danger-400' : 'border-neutral-300',
                          )} />
                {submitted && missing.notes && <p className="text-xs text-danger-600">Say what the credit is for and who confirmed it</p>}
              </div>

              <div className="flex flex-col gap-1">
                <span className="text-xs font-medium text-neutral-700">
                  Evidence <span className="ml-0.5 text-danger-600">*</span>
                </span>
                <label className={cn(
                  'flex cursor-pointer items-center gap-2 rounded-lg border border-dashed px-3 py-3 text-sm text-neutral-600 hover:bg-neutral-50',
                  submitted && missing.files ? 'border-danger-400' : 'border-neutral-300',
                )}>
                  <Paperclip className="h-4 w-4 text-neutral-400" />
                  <span>
                    Attach the vendor's email — saved from Outlook (.msg / .eml), or printed to PDF, or a screenshot
                  </span>
                  <input type="file" multiple accept={CREDIT_EVIDENCE_ACCEPT} className="hidden"
                         onChange={(e) => { addFiles(e.target.files); e.target.value = '' }} />
                </label>
                {files.length > 0 && (
                  <ul className="flex flex-col gap-1">
                    {files.map((f) => (
                      <li key={`${f.name}-${f.size}`} className="flex items-center justify-between rounded bg-neutral-50 px-2 py-1 text-xs">
                        <span className="truncate">{f.name}</span>
                        <button type="button" aria-label={`Remove ${f.name}`}
                                onClick={() => setFiles((prev) => prev.filter((p) => p !== f))}
                                className="text-neutral-400 hover:text-danger-600">
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {submitted && missing.files && <p className="text-xs text-danger-600">Attach the vendor's confirmation</p>}
              </div>

              {error && (
                <div className="flex items-center gap-2 rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                  {error}
                </div>
              )}
            </div>

            <div className="flex items-center justify-between border-t border-neutral-100 px-6 py-3">
              <p className="text-xs text-neutral-400">Goes to Pending Review — it is not netted off any payment until approved.</p>
              <div className="flex gap-2">
                <button onClick={onClose} disabled={busy}
                        className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50">
                  Cancel
                </button>
                <button onClick={() => void submit()} disabled={busy || create.isPending}
                        className="rounded-lg bg-primary-600 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
                  {busy ? 'Recording…' : 'Record Credit'}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  )
}
