import { useEffect, useRef, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { createPortal } from 'react-dom'
import { useReplaceTab } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'
import { ArrowLeft, Search, X } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FormField } from '@/components/ui/form-field'
import { cn } from '@/lib/utils'
import { useAgreement, useUpdateAgreement } from '@/hooks/useAgreements'
import { agreementService, type AgreementType } from '@/services/agreement'
import { useDepartments } from '@/hooks/useDepartments'
import { useTaxCodes } from '@/hooks/useTaxCodes'
import { userService, type ApiUserBrief } from '@/services/users'

const TYPE_LABELS: Record<AgreementType, string> = {
  house_account: 'House Account',
  recurring: 'Recurring',
  milestone: 'Milestone',
}

// ─── Anchored search dropdown (Owner only — Vendor/Type/Currency are locked
// post-creation, see AgreementUpdate on the backend) ────────────────────────

function useAnchorRect<T extends HTMLElement>(open: boolean, anchorRef: React.RefObject<T | null>) {
  const [rect, setRect] = useState<DOMRect | null>(null)
  useEffect(() => {
    if (!open) { setRect(null); return }
    const update = () => setRect(anchorRef.current?.getBoundingClientRect() ?? null)
    update()
    window.addEventListener('scroll', update, true)
    window.addEventListener('resize', update)
    return () => {
      window.removeEventListener('scroll', update, true)
      window.removeEventListener('resize', update)
    }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
  return rect
}

function DropdownPortal({ anchorRect, onClose, children }: { anchorRect: DOMRect; onClose: () => void; children: React.ReactNode }) {
  return createPortal(
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div
        style={{ position: 'fixed', top: anchorRect.bottom + 4, left: anchorRect.left, width: anchorRect.width }}
        className="z-50 max-h-64 overflow-y-auto rounded-lg border border-neutral-200 bg-white py-1 shadow-lg"
      >
        {children}
      </div>
    </>,
    document.body
  )
}

export default function AgreementEditPage() {
  const { id } = useParams<{ id: string }>()
  const replaceTab = useReplaceTab(epmsRoutes)
  const { data: agreement, isLoading } = useAgreement(id ?? '')
  const updateAgreement = useUpdateAgreement()
  const { data: deptData } = useDepartments()
  const departments = (deptData?.items ?? []).filter((d) => d.is_active)
  const taxCodes = useTaxCodes()

  const [title, setTitle] = useState('')
  const [contractNo, setContractNo] = useState('')
  const [contactEmail, setContactEmail] = useState('')
  const [vendorReference, setVendorReference] = useState('')
  const [validFrom, setValidFrom] = useState('')
  const [validTo, setValidTo] = useState('')
  const [graceDays, setGraceDays] = useState(30)
  const [notToExceed, setNotToExceed] = useState('')
  const [taxCode, setTaxCode] = useState<string | null>(null)
  const [taxRate, setTaxRate] = useState<number | null>(null)
  const [departmentId, setDepartmentId] = useState('')
  const [budgetCode, setBudgetCode] = useState('')
  const [notes, setNotes] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Owner picker
  const ownerAnchorRef = useRef<HTMLDivElement>(null)
  const [ownerOpen, setOwnerOpen] = useState(false)
  const ownerRect = useAnchorRect(ownerOpen, ownerAnchorRef)
  const [ownerQuery, setOwnerQuery] = useState('')
  const [selectedOwner, setSelectedOwner] = useState<ApiUserBrief | null>(null)
  const { data: ownersData } = useQuery({
    queryKey: ['users', 'directory', ownerQuery],
    queryFn: () => userService.directory({ search: ownerQuery || undefined }),
    enabled: ownerOpen,
    staleTime: 30_000,
  })
  const owners = ownersData?.items ?? []

  // Pre-fill from the existing agreement
  useEffect(() => {
    if (!agreement) return
    setTitle(agreement.title)
    setContractNo(agreement.contract_no ?? '')
    setContactEmail(agreement.contact_email ?? '')
    setVendorReference(agreement.vendor_reference ?? '')
    setValidFrom(agreement.valid_from)
    setValidTo(agreement.valid_to)
    setGraceDays(agreement.grace_days)
    setNotToExceed(agreement.not_to_exceed ?? '')
    setTaxCode(agreement.tax_code ?? null)
    setTaxRate(agreement.tax_rate ? Number(agreement.tax_rate) : null)
    setDepartmentId(agreement.department_id ?? '')
    setBudgetCode(agreement.budget_code ?? '')
    setNotes(agreement.notes ?? '')
    // Note: agreement.owner_id (a raw UUID, no name) is intentionally not
    // resolved into the picker — same reasoning as AgreementDetailPage: there
    // is no non-admin-safe endpoint to turn an id into a display name. Leaving
    // owner blank here means "no change" (owner_id is omitted from the PATCH
    // body unless the user actively searches and picks someone).
  }, [agreement?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const validate = (): boolean => {
    const e: Record<string, string> = {}
    if (!title.trim()) e.title = 'Title is required'
    if (!validFrom) e.validFrom = 'Valid From date is required'
    if (!validTo) e.validTo = 'Valid To date is required'
    if (validFrom && validTo && validTo < validFrom) e.validTo = 'Valid To must be on or after Valid From'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSave = async (andSubmit: boolean) => {
    if (!validate()) return
    setIsSubmitting(true)
    try {
      await updateAgreement.mutateAsync({
        id: id!,
        // Cleared fields must go out as explicit `null`, NOT `undefined`.
        // JSON.stringify drops undefined keys, and the backend uses
        // model_dump(exclude_unset=True) — so an undefined here means "don't
        // touch", and clearing Not-to-Exceed / tax code / department / owner
        // appeared to save while keeping the old value. All these columns are
        // nullable. PoEditPage sends null for the same reason.
        body: {
          title,
          contract_no: contractNo || null,
          contact_email: contactEmail || null,
          vendor_reference: vendorReference || null,
          valid_from: validFrom,
          valid_to: validTo,
          grace_days: graceDays,
          not_to_exceed: notToExceed ? Number(notToExceed) : null,
          tax_code: taxCode ?? null,
          tax_rate: taxRate ?? null,
          department_id: departmentId || null,
          budget_code: budgetCode || null,
          owner_id: selectedOwner?.id || null,
          notes: notes || null,
        },
      })
      if (andSubmit) {
        await agreementService.action(id!, { action: 'submit' })
      }
      replaceTab(`/agreements/${id}`)
    } finally {
      setIsSubmitting(false)
    }
  }

  if (isLoading) {
    return <div className="p-8 text-sm text-neutral-400">Loading…</div>
  }

  if (!agreement || !['draft', 'returned'].includes(agreement.status)) {
    return (
      <div className="p-8">
        <p className="text-sm text-neutral-500">This agreement cannot be edited in its current status.</p>
        <Link to={`/agreements/${id}`} className="mt-3 inline-block text-sm text-primary-600 hover:underline">← Back to Agreement</Link>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-3">
        <Link to={`/agreements/${id}`}>
          <Button variant="ghost" size="icon-sm"><ArrowLeft className="h-4 w-4" /></Button>
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Edit Agreement</h1>
          <p className="text-sm text-neutral-400 mt-0.5">{agreement.number}</p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        <div className="lg:col-span-9 flex flex-col gap-6">

          {/* Basic details */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-5">
            <h2 className="text-base font-semibold text-neutral-900">Agreement Details</h2>

            <FormField label="Title" required htmlFor="title" error={errors.title}>
              <Input
                id="title"
                value={title}
                onChange={(e) => { setTitle(e.target.value); setErrors((p) => ({ ...p, title: '' })) }}
                error={!!errors.title}
              />
            </FormField>

            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
              {/* Vendor + Type are locked after creation — changing counterparty or
                  category means a new agreement, not an edit (backend AgreementUpdate
                  has no vendor_id/agreement_type field). Shown read-only for context. */}
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">Vendor</label>
                <div className="flex h-10 items-center rounded-md border border-neutral-200 bg-neutral-100 px-3 text-sm text-neutral-600">
                  {agreement.vendor_name}
                </div>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">Type</label>
                <div className="flex h-10 items-center rounded-md border border-neutral-200 bg-neutral-100 px-3 text-sm text-neutral-600">
                  {TYPE_LABELS[agreement.agreement_type]}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
              <FormField label="Contract No." htmlFor="contractNo">
                <Input id="contractNo" value={contractNo} onChange={(e) => setContractNo(e.target.value)} />
              </FormField>
              <FormField label="Vendor Contact Email" htmlFor="contactEmail">
                <Input id="contactEmail" type="email" value={contactEmail} onChange={(e) => setContactEmail(e.target.value)} />
              </FormField>
            </div>

            <FormField
              label="Vendor Reference"
              htmlFor="vendorReference"
              hint="The account or reference number the vendor prints on their invoices."
            >
              <Input id="vendorReference" value={vendorReference} onChange={(e) => setVendorReference(e.target.value)} />
            </FormField>

            <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">Valid From <span className="text-danger-600">*</span></label>
                <input
                  type="date"
                  value={validFrom}
                  onChange={(e) => { setValidFrom(e.target.value); setErrors((p) => ({ ...p, validFrom: '' })) }}
                  className={cn(
                    'h-10 w-full rounded-md border bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
                    errors.validFrom ? 'border-danger-600' : 'border-neutral-300'
                  )}
                />
                {errors.validFrom && <p className="text-xs text-danger-600">{errors.validFrom}</p>}
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">Valid To <span className="text-danger-600">*</span></label>
                <input
                  type="date"
                  value={validTo}
                  onChange={(e) => { setValidTo(e.target.value); setErrors((p) => ({ ...p, validTo: '' })) }}
                  className={cn(
                    'h-10 w-full rounded-md border bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
                    errors.validTo ? 'border-danger-600' : 'border-neutral-300'
                  )}
                />
                {errors.validTo && <p className="text-xs text-danger-600">{errors.validTo}</p>}
              </div>
              <FormField label="Grace Days" htmlFor="graceDays">
                <Input
                  id="graceDays"
                  type="number"
                  min={0}
                  max={365}
                  value={graceDays}
                  onChange={(e) => setGraceDays(Number(e.target.value) || 0)}
                />
              </FormField>
            </div>
          </div>

          {/* Financial terms */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-5">
            <h2 className="text-base font-semibold text-neutral-900">Financial Terms</h2>

            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
              <FormField
                label="Not to Exceed"
                htmlFor="notToExceed"
                hint="Optional ceiling. Spend past this amount only warns — it never blocks matching or approval."
              >
                <Input
                  id="notToExceed"
                  type="number"
                  min={0}
                  step="0.01"
                  placeholder="Leave blank for no ceiling"
                  value={notToExceed}
                  onChange={(e) => setNotToExceed(e.target.value)}
                />
              </FormField>

              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">Currency</label>
                <div className="flex h-10 items-center rounded-md border border-neutral-200 bg-neutral-100 px-3 text-sm text-neutral-600">
                  {agreement.currency}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">Tax Code</label>
                <select
                  value={taxCode ?? ''}
                  onChange={(e) => {
                    const c = taxCodes.find((tc) => tc.code === e.target.value)
                    setTaxCode(c?.code ?? null)
                    setTaxRate(c ? Number(c.rate) : null)
                  }}
                  className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                >
                  <option value="">No tax</option>
                  {taxCode && !taxCodes.some((c) => c.code === taxCode) && (
                    <option value={taxCode}>{taxCode} (inactive)</option>
                  )}
                  {taxCodes.map((c) => (
                    <option key={c.code} value={c.code}>
                      {+(Number(c.rate) * 100).toFixed(3)}% — {c.name}
                    </option>
                  ))}
                </select>
              </div>

              <FormField label="Budget Code" htmlFor="budgetCode">
                <Input id="budgetCode" value={budgetCode} onChange={(e) => setBudgetCode(e.target.value)} />
              </FormField>
            </div>

            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">Department</label>
                <select
                  value={departmentId}
                  onChange={(e) => setDepartmentId(e.target.value)}
                  className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                >
                  <option value="">No department</option>
                  {departments.map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
              </div>

              {/* Owner */}
              <div ref={ownerAnchorRef} className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">Owner</label>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
                  <input
                    type="text"
                    placeholder={agreement.owner_id && !selectedOwner ? 'Search to change owner…' : 'Search people by name…'}
                    value={selectedOwner ? selectedOwner.full_name : ownerQuery}
                    onFocus={() => { setOwnerOpen(true); if (selectedOwner) setOwnerQuery('') }}
                    onChange={(e) => { setOwnerQuery(e.target.value); setSelectedOwner(null); setOwnerOpen(true) }}
                    className="h-10 w-full rounded-md border border-neutral-300 bg-white pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                  />
                  {selectedOwner && (
                    <button
                      type="button"
                      onClick={() => { setSelectedOwner(null); setOwnerQuery('') }}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
                {ownerOpen && !selectedOwner && ownerRect && (
                  <DropdownPortal anchorRect={ownerRect} onClose={() => setOwnerOpen(false)}>
                    {owners.map((u) => (
                      <button
                        key={u.id}
                        type="button"
                        className="flex w-full items-center justify-between px-3 py-2 text-sm hover:bg-primary-50 text-left"
                        onClick={() => { setSelectedOwner(u); setOwnerOpen(false); setOwnerQuery('') }}
                      >
                        <span>{u.full_name}</span>
                        {u.department_name && <span className="text-xs text-neutral-400">{u.department_name}</span>}
                      </button>
                    ))}
                    {owners.length === 0 && (
                      <p className="px-3 py-2 text-sm text-neutral-400">No people found</p>
                    )}
                  </DropdownPortal>
                )}
                {agreement.owner_id && !selectedOwner && (
                  <p className="text-xs text-neutral-400">
                    An owner is already set. Leave blank to keep it, or search to replace it.
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* Notes */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-4">
            <h2 className="text-base font-semibold text-neutral-900">Notes</h2>
            <FormField label="Internal Notes" htmlFor="notes">
              <textarea
                id="notes"
                rows={3}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                className="w-full resize-none rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
              />
            </FormField>
          </div>

          {/* Footer actions */}
          <div className="flex items-center justify-between rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] px-6 py-4">
            <Link to={`/agreements/${id}`}>
              <Button variant="ghost">Cancel</Button>
            </Link>
            <div className="flex items-center gap-3">
              <Button variant="secondary" onClick={() => handleSave(false)} disabled={isSubmitting}>
                Save Draft
              </Button>
              <Button onClick={() => handleSave(true)} disabled={isSubmitting}>
                {isSubmitting ? 'Submitting…' : 'Save & Submit'}
              </Button>
            </div>
          </div>
        </div>

        {/* Sidebar */}
        <div className="lg:col-span-3">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5 sticky top-6">
            <h3 className="text-sm font-semibold text-neutral-700 mb-3">Agreement Info</h3>
            <dl className="flex flex-col gap-2 text-xs">
              <div className="flex justify-between">
                <dt className="text-neutral-500">Number</dt>
                <dd className="font-mono text-neutral-700">{agreement.number}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-neutral-500">Status</dt>
                <dd className="capitalize text-neutral-700">{agreement.status}</dd>
              </div>
            </dl>
          </div>
        </div>
      </div>
    </div>
  )
}
