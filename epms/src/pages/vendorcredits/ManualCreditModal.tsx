import { useState, useRef } from 'react'
import { AlertTriangle, Mail, Paperclip, Search, X } from 'lucide-react'

import { useCreateVendorCredit } from '@/hooks/useVendorCredits'
import { useVendors } from '@/hooks/useVendors'
import { cn, formatAmount, todayISO } from '@/lib/utils'
import { Button, DropdownPortal, FileInput, Input, MenuItem, Modal, Select, Textarea, useAnchorRect } from '@uniops/shell'
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

// The plant's date (company time zone). Not toISOString(): that is the UTC
// date, which is tomorrow after ~20:00 here.
const localToday = () => todayISO()

type Vendor = { id: string; name: string; code: string }

interface Props {
  onClose: () => void
  onCreated: (credit: VendorCredit) => void
}

export function ManualCreditModal({ onClose, onCreated }: Props) {
  const [vendor, setVendor] = useState<Vendor | null>(null)
  const [vendorQuery, setVendorQuery] = useState('')
  const [vendorOpen, setVendorOpen] = useState(false)
  const vendorAnchorRef = useRef<HTMLDivElement>(null)
  const vendorRect = useAnchorRect(vendorOpen, vendorAnchorRef)
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

  return (
    <Modal
      open
      onClose={onClose}
      busy={busy}
      // Closed only by its buttons, as before.
      closeOnEscape={false}
      size="lg"
      icon={<Mail />}
      title="Record Manual Credit"
      description="For a credit the vendor confirmed in writing but will not issue a credit note for"
      // The two views keep their own scroll area and footer, as before.
      bodyClassName="flex min-h-0 flex-col overflow-hidden p-0"
    >

        {partial ? (
          <div className="flex flex-col gap-3 px-6 py-5 text-sm">
            <div className="flex items-start gap-2 rounded-lg border border-warning-line bg-warning-soft px-3 py-2 text-warning-fg">
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
              <Button size="md"
                      onClick={() => onCreated(partial.credit)}>
                OK
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-4 overflow-y-auto px-6 py-5">
              <div className="grid grid-cols-2 gap-3">
                <div className="relative flex flex-col gap-1">
                  <label className="text-xs font-medium text-fg-secondary">
                    Vendor <span className="ml-0.5 text-danger-fg">*</span>
                  </label>
                  <div className="relative" ref={vendorAnchorRef}>
                    <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-muted" />
                    <Input
                      type="text" placeholder="Search vendor by name or code…"
                      value={vendor ? vendor.name : vendorQuery}
                      onFocus={() => { setVendorOpen(true); if (vendor) setVendorQuery('') }}
                      onChange={(e) => { setVendorQuery(e.target.value); setVendor(null); setVendorOpen(true) }}
                      error={submitted && missing.vendor} className="pl-8 pr-8"
                    />
                    {vendor && (
                      <Button variant="subtle" size="icon-xs" type="button" aria-label="Clear vendor"
                              onClick={() => { setVendor(null); setVendorQuery('') }}
                              className="absolute right-1.5 top-1/2 -translate-y-1/2">
                        <X className="h-3.5 w-3.5" />
                      </Button>
                    )}
                  </div>
                  {submitted && missing.vendor && <p className="text-xs text-danger-fg">Select a vendor</p>}
                  {vendorOpen && !vendor && vendorRect && (
                    <>
                      <DropdownPortal anchorRect={vendorRect} onClose={() => setVendorOpen(false)} className="max-h-44 overflow-y-auto">
                        {vendorOptions.map((v) => (
                          <MenuItem key={v.id} type="button"
                                  onClick={() => { setVendor({ id: v.id, name: v.name, code: v.code }); setVendorOpen(false); setVendorQuery('') }}
                                 >
                            <span className="rounded bg-surface-sunken px-1.5 py-0.5 font-mono text-xs text-fg-secondary">{v.code}</span>
                            {v.name}
                          </MenuItem>
                        ))}
                        {vendorOptions.length === 0 && <p className="px-3 py-2 text-xs text-fg-muted">No vendors found</p>}
                      </DropdownPortal>
                    </>
                  )}
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-fg-secondary">
                    Vendor Reference # <span className="ml-0.5 text-danger-fg">*</span>
                  </label>
                  <Input value={reference} onChange={(e) => setReference(e.target.value)}
                         placeholder="e.g. 8480321003REV" error={submitted && missing.reference} className="font-mono" />
                  <p className="text-2xs text-fg-muted">
                    The number the vendor asked you to quote. It goes on the remittance, and blocks recording the same credit twice.
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-4 gap-3">
                <div className="col-span-2 flex flex-col gap-1">
                  <label className="text-xs font-medium text-fg-secondary">
                    Credit Date <span className="ml-0.5 text-danger-fg">*</span>
                  </label>
                  <Input type="date" value={creditDate} onChange={(e) => setCreditDate(e.target.value)}
                         error={submitted && missing.date} />
                  <p className="text-2xs text-fg-muted">Usually the date of the vendor's email.</p>
                </div>
                <div className="col-span-2 flex flex-col gap-1">
                  <label className="text-xs font-medium text-fg-secondary">Currency</label>
                  <Select value={currency} onChange={(e) => setCurrency(e.target.value)} >
                    {CURRENCIES.map((c) => <option key={c}>{c}</option>)}
                  </Select>
                  <p className="text-2xs text-fg-muted">Only nets off payments in the same currency.</p>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-fg-secondary">
                    Amount (pre-tax) <span className="ml-0.5 text-danger-fg">*</span>
                  </label>
                  <Input type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)}
                         placeholder="2216.15" error={submitted && missing.amount} className="font-mono" />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-fg-secondary">Tax</label>
                  <Input type="number" step="0.01" value={tax} onChange={(e) => setTax(e.target.value)}
                         placeholder="0.00" className="font-mono" />
                </div>
                <div className="flex flex-col gap-1">
                  <span className="text-xs font-medium text-fg-secondary">Credit Total</span>
                  <span className="flex h-9 items-center font-mono font-semibold text-fg">
                    {formatAmount(total, currency)}
                  </span>
                </div>
              </div>
              <p className="-mt-2 text-2xs text-fg-muted">
                Enter the figure as the vendor quoted it — a minus sign is fine, credits are always stored as positive.
                If the vendor gave one all-in figure, put it all in Amount.
              </p>

              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-fg-secondary">PO # (optional)</label>
                <Input value={poNumber} onChange={(e) => setPoNumber(e.target.value)}
                       className="font-mono" />
              </div>

              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-fg-secondary">
                  Basis <span className="ml-0.5 text-danger-fg">*</span>
                </label>
                <Textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)}
                          placeholder="Why this credit exists and who confirmed it — e.g. Duplicate payment of invoice 8480321003. Hema Nanjiani (MSC collections) confirmed by email on 2026-09-25 that no credit memo will be issued; deduct on next payment."
                          error={!!(submitted && missing.notes)} className="p-2" />
                {submitted && missing.notes && <p className="text-xs text-danger-fg">Say what the credit is for and who confirmed it</p>}
              </div>

              <div className="flex flex-col gap-1">
                <span className="text-xs font-medium text-fg-secondary">
                  Evidence <span className="ml-0.5 text-danger-fg">*</span>
                </span>
                <label className={cn(
                  'flex cursor-pointer items-center gap-2 rounded-lg border border-dashed px-3 py-3 text-sm text-fg-secondary hover:bg-surface-subtle',
                  submitted && missing.files ? 'border-danger-400' : 'border-line-strong',
                )}>
                  <Paperclip className="h-4 w-4 text-fg-muted" />
                  <span>
                    Attach the vendor's email — saved from Outlook (.msg / .eml), or printed to PDF, or a screenshot
                  </span>
                  <FileInput multiple accept={CREDIT_EVIDENCE_ACCEPT} className="hidden"
                         onChange={(e) => { addFiles(e.target.files); e.target.value = '' }} />
                </label>
                {files.length > 0 && (
                  <ul className="flex flex-col gap-1">
                    {files.map((f) => (
                      <li key={`${f.name}-${f.size}`} className="flex items-center justify-between rounded bg-surface-subtle px-2 py-1 text-xs">
                        <span className="truncate">{f.name}</span>
                        <Button variant="danger-ghost" size="icon-xs" type="button" aria-label={`Remove ${f.name}`}
                                onClick={() => setFiles((prev) => prev.filter((p) => p !== f))}
                               >
                          <X className="h-3.5 w-3.5" />
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
                {submitted && missing.files && <p className="text-xs text-danger-fg">Attach the vendor's confirmation</p>}
              </div>

            </div>

            {/* Outside the scrolling body, directly above Record Credit — inside
                it the refusal landed below the fold and the click looked like it
                did nothing (ISS-0115). */}
            {error && (
              <div className="mx-6 mt-3 flex items-center gap-2 rounded-lg border border-danger-line bg-danger-soft px-3 py-2 text-xs text-danger-fg" role="alert">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                {error}
              </div>
            )}

            <div className="flex items-center justify-between border-t border-line-subtle px-6 py-3">
              <p className="text-xs text-fg-muted">Goes to Pending Review — it is not netted off any payment until approved.</p>
              <div className="flex gap-2">
                <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}
                       >
                  Cancel
                </Button>
                <Button size="sm" onClick={() => void submit()} disabled={busy || create.isPending}
                       >
                  {busy ? 'Recording…' : 'Record Credit'}
                </Button>
              </div>
            </div>
          </>
        )}
    </Modal>
  )
}
