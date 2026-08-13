import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useReplaceTab } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'
import { ArrowLeft, Search, Upload, X } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FormField } from '@/components/ui/form-field'
import { DropdownPortal, useAnchorRect } from '@/components/ui/DropdownPortal'
import { useConfig } from '@/hooks/useConfig'
import { cn } from '@/lib/utils'
import { CURRENCIES } from '@/types'
import type { Currency } from '@/types'
import { useCreateAgreement } from '@/hooks/useAgreements'
import { agreementService, type AgreementType, type CreateAgreementBody, type MilestoneRowIn } from '@/services/agreement'
import { agreementAttachmentService } from '@/services/agreementAttachments'
import { useVendors } from '@/hooks/useVendors'
import type { ApiVendor } from '@/services/vendors'
import { useDepartments } from '@/hooks/useDepartments'
import { useTaxCodes } from '@/hooks/useTaxCodes'
import { userService, type ApiUserBrief } from '@/services/users'
import { BudgetAccountCascade } from '@/components/agreements/BudgetAccountCascade'
import { RecurringFields, EMPTY_RECURRING_FIELDS, type RecurringFieldsValue } from '@/components/agreements/RecurringFields'
import { MilestoneEditor } from '@/components/agreements/MilestoneEditor'

const TYPE_OPTIONS: { value: AgreementType; label: string; hint: string }[] = [
  // Whole-branch review (M8): a house account is not necessarily a counter
  // pickup — counter slips, delivery notes and service sign-offs are all
  // valid evidence on one (see ReceiptType). The hint must not narrow the
  // type down to the one customer that inspired it.
  { value: 'house_account', label: 'House Account', hint: 'Ongoing spend on a running account with a vendor, billed periodically — evidenced by receipts, delivery notes or service sign-offs' },
  { value: 'recurring', label: 'Recurring', hint: 'A repeating service or subscription-style spend' },
  { value: 'milestone', label: 'Milestone', hint: 'Spend released against agreed project milestones' },
]

export default function AgreementCreatePage() {
  const replaceTab = useReplaceTab(epmsRoutes)
  const createAgreement = useCreateAgreement()
  const { data: config } = useConfig()
  const { data: deptData } = useDepartments()
  const departments = (deptData?.items ?? []).filter((d) => d.is_active)
  const taxCodes = useTaxCodes()

  // Basic fields
  const [title, setTitle] = useState('')
  const [agreementType, setAgreementType] = useState<AgreementType>('house_account')
  const [contractNo, setContractNo] = useState('')
  const [contactEmail, setContactEmail] = useState('')
  const [vendorReference, setVendorReference] = useState('')
  const [validFrom, setValidFrom] = useState('')
  const [validTo, setValidTo] = useState('')
  const [graceDays, setGraceDays] = useState(30)
  const [notToExceed, setNotToExceed] = useState('')
  const [currency, setCurrency] = useState<Currency>('CAD')
  const [taxCode, setTaxCode] = useState<string | null>(null)
  const [taxRate, setTaxRate] = useState<number | null>(null)
  const [departmentId, setDepartmentId] = useState('')
  const [costCenterId, setCostCenterId] = useState<string | undefined>(undefined)
  const [budgetCode, setBudgetCode] = useState('')
  const [notes, setNotes] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Recurring / milestone type-specific state. Only one of these is ever sent
  // to the server — switching `agreementType` clears the other, or the
  // backend's "only apply to a recurring agreement" / "milestones can only
  // be set on a milestone agreement" rules 422 (schemas/agreement.py).
  const [recurringValue, setRecurringValue] = useState<RecurringFieldsValue>(EMPTY_RECURRING_FIELDS)
  const [milestoneRows, setMilestoneRows] = useState<MilestoneRowIn[]>([])

  const handleAgreementTypeChange = (next: AgreementType) => {
    setAgreementType(next)
    if (next !== 'recurring') setRecurringValue(EMPTY_RECURRING_FIELDS)
    if (next !== 'milestone') setMilestoneRows([])
    setErrors((p) => ({ ...p, recurring: '', milestones: '' }))
  }

  // Attachments — same shape/upload sequencing as PrCreatePage.tsx:97,335.
  const [attachments, setAttachments] = useState<File[]>([])
  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    setAttachments((prev) => [...prev, ...files])
    e.target.value = ''
  }

  // Vendor picker
  const vendorAnchorRef = useRef<HTMLDivElement>(null)
  const [vendorOpen, setVendorOpen] = useState(false)
  const vendorRect = useAnchorRect(vendorOpen, vendorAnchorRef)
  const [vendorQuery, setVendorQuery] = useState('')
  const [selectedVendor, setSelectedVendor] = useState<ApiVendor | null>(null)
  const { data: vendorsData } = useVendors({ active_only: true, search: vendorQuery || undefined, page_size: 50 })
  const vendors = vendorsData?.items ?? []

  // Owner picker (searches the public user directory, same as invoice re-assignment)
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

  // Default-select the tax code matching the default rate once codes load,
  // mirroring PoCreatePage — only while nothing has been chosen yet.
  useEffect(() => {
    if (currency !== 'CAD' || taxCode || taxCodes.length === 0) return
    const match = taxCodes[0]
    if (match) { setTaxCode(match.code); setTaxRate(Number(match.rate)) }
  }, [taxCodes, currency]) // eslint-disable-line react-hooks/exhaustive-deps

  const validate = (): boolean => {
    const e: Record<string, string> = {}
    if (!title.trim()) e.title = 'Title is required'
    if (!selectedVendor) e.vendor = 'Please select a vendor'
    if (!validFrom) e.validFrom = 'Valid From date is required'
    if (!validTo) e.validTo = 'Valid To date is required'
    if (validFrom && validTo && validTo < validFrom) e.validTo = 'Valid To must be on or after Valid From'
    if (agreementType === 'recurring') {
      if (!recurringValue.recurringType) {
        e.recurring = 'Cycle is required for a recurring agreement'
      } else if (!recurringValue.expectedInvoiceDay) {
        e.recurring = 'Expected invoice day is required'
      } else if (
        (recurringValue.recurringType === 'quarterly' || recurringValue.recurringType === 'yearly') &&
        !recurringValue.anchorMonth
      ) {
        e.recurring = 'Anchor month is required for a quarterly/yearly cycle'
      }
    }
    if (agreementType === 'milestone' && milestoneRows.some((r) => !r.milestone_name.trim())) {
      e.milestones = 'Every stage needs a name'
    }
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSubmit = async (submitForApproval: boolean) => {
    if (!validate()) return
    setIsSubmitting(true)
    try {
      const body: CreateAgreementBody = {
        title,
        agreement_type: agreementType,
        vendor_id: selectedVendor!.id,
        contract_no: contractNo || undefined,
        contact_email: contactEmail || undefined,
        vendor_reference: vendorReference || undefined,
        valid_from: validFrom,
        valid_to: validTo,
        grace_days: graceDays,
        not_to_exceed: notToExceed ? Number(notToExceed) : undefined,
        currency,
        tax_code: taxCode ?? undefined,
        tax_rate: taxRate ?? undefined,
        department_id: departmentId || undefined,
        budget_code: budgetCode || undefined,
        owner_id: selectedOwner?.id || undefined,
        notes: notes || undefined,
        cost_center_id: costCenterId || undefined,
      }
      // Only the fields for the CHOSEN type ever go out — the backend 422s a
      // non-recurring agreement carrying any recurring_type/expected_invoice_day/
      // anchor_month/expected_amount_per_period/tolerance_pct/overdue_after_days,
      // and a non-milestone agreement carrying milestones.
      if (agreementType === 'recurring') {
        body.recurring_type = recurringValue.recurringType || undefined
        body.expected_invoice_day = recurringValue.expectedInvoiceDay ? Number(recurringValue.expectedInvoiceDay) : undefined
        body.anchor_month = recurringValue.anchorMonth ? Number(recurringValue.anchorMonth) : undefined
        body.schedule_start_date = recurringValue.scheduleStartDate || undefined
        body.expected_amount_per_period = recurringValue.amountPerPeriod ? Number(recurringValue.amountPerPeriod) : undefined
        body.tolerance_pct = recurringValue.tolerancePct ? Number(recurringValue.tolerancePct) : undefined
        body.overdue_after_days = recurringValue.overdueAfterDays ? Number(recurringValue.overdueAfterDays) : undefined
      } else if (agreementType === 'milestone') {
        body.milestones = milestoneRows
          .filter((r) => r.milestone_name.trim())
          .map((r) => ({
            milestone_name: r.milestone_name.trim(),
            expected_timing: r.expected_timing?.trim() || null,
            expected_amount: r.expected_amount || null,
            // amount_pct only survives if a ceiling is actually set — the
            // backend rejects any stage's amount_pct when not_to_exceed is
            // null, and notToExceed can be cleared in the same session after
            // a percentage was already entered (MilestoneEditor disables the
            // % input going forward but doesn't retroactively wipe stale
            // values already sitting in state).
            amount_pct: notToExceed ? (r.amount_pct || null) : null,
          }))
      }

      const newAgreement = await createAgreement.mutateAsync(body)
      if (attachments.length > 0) {
        // The agreement already exists at this point — an upload failure
        // here must not read as "nothing happened". Catch it, tell the user
        // explicitly which half succeeded, and keep going (submit action +
        // navigate) rather than leaving them on a form for a document that
        // was, in fact, already created.
        try {
          await Promise.all(attachments.map((f) => agreementAttachmentService.upload(newAgreement.id, f)))
        } catch {
          alert(
            `${newAgreement.number} was created, but one or more attachments failed to upload. ` +
            'You can add them again from the agreement page.'
          )
        }
      }
      if (submitForApproval) {
        await agreementService.action(newAgreement.id, { action: 'submit' })
      }
      replaceTab(`/agreements/${newAgreement.id}`)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div className="flex items-center gap-3">
        <Link to="/agreements">
          <Button variant="ghost" size="icon-sm">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <h1 className="text-2xl font-bold text-neutral-900">New Agreement</h1>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Main form */}
        <div className="lg:col-span-9 flex flex-col gap-6">

          {/* Basic details */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-5">
            <h2 className="text-base font-semibold text-neutral-900">Agreement Details</h2>

            <FormField label="Title" required htmlFor="title" error={errors.title}>
              <Input
                id="title"
                placeholder="e.g. Acme Hardware — House Account"
                value={title}
                onChange={(e) => { setTitle(e.target.value); setErrors((p) => ({ ...p, title: '' })) }}
                error={!!errors.title}
              />
            </FormField>

            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
              {/* Vendor */}
              <div ref={vendorAnchorRef} className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">
                  Vendor <span className="text-danger-600">*</span>
                </label>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
                  <input
                    type="text"
                    placeholder="Search vendor by name or code…"
                    value={selectedVendor ? selectedVendor.name : vendorQuery}
                    onFocus={() => { setVendorOpen(true); if (selectedVendor) setVendorQuery('') }}
                    onChange={(e) => { setVendorQuery(e.target.value); setSelectedVendor(null); setVendorOpen(true); setErrors((p) => ({ ...p, vendor: '' })) }}
                    className={cn(
                      'h-10 w-full rounded-md border bg-white pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
                      errors.vendor ? 'border-danger-600' : 'border-neutral-300'
                    )}
                  />
                  {selectedVendor && (
                    <button
                      type="button"
                      onClick={() => { setSelectedVendor(null); setVendorQuery('') }}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
                {vendorOpen && !selectedVendor && vendorRect && (
                  <DropdownPortal anchorRect={vendorRect} onClose={() => setVendorOpen(false)}>
                    {vendors.map((v) => (
                      <button
                        key={v.id}
                        type="button"
                        className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-primary-50 text-left"
                        onClick={() => { setSelectedVendor(v); setVendorOpen(false); setVendorQuery(''); setErrors((p) => ({ ...p, vendor: '' })) }}
                      >
                        <span className="font-mono text-xs rounded bg-neutral-100 px-1.5 py-0.5 text-neutral-600">{v.code}</span>
                        {v.name}
                      </button>
                    ))}
                    {vendors.length === 0 && (
                      <p className="px-3 py-2 text-sm text-neutral-400">No vendors found</p>
                    )}
                  </DropdownPortal>
                )}
                {selectedVendor && <p className="text-xs text-success-600">✓ {selectedVendor.name} ({selectedVendor.code})</p>}
                {errors.vendor && <p className="text-xs text-danger-600">{errors.vendor}</p>}
              </div>

              {/* Agreement Type */}
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">Type <span className="text-danger-600">*</span></label>
                <select
                  value={agreementType}
                  onChange={(e) => handleAgreementTypeChange(e.target.value as AgreementType)}
                  className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                >
                  {TYPE_OPTIONS.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
                <p className="text-xs text-neutral-400">
                  {TYPE_OPTIONS.find((t) => t.value === agreementType)?.hint}
                </p>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
              <FormField label="Contract No." htmlFor="contractNo">
                <Input
                  id="contractNo"
                  placeholder="e.g. CT-2026-014"
                  value={contractNo}
                  onChange={(e) => setContractNo(e.target.value)}
                />
              </FormField>
              <FormField label="Vendor Contact Email" htmlFor="contactEmail">
                <Input
                  id="contactEmail"
                  type="email"
                  placeholder="ap@vendor.com"
                  value={contactEmail}
                  onChange={(e) => setContactEmail(e.target.value)}
                />
              </FormField>
            </div>

            <FormField
              label="Vendor Reference"
              htmlFor="vendorReference"
              hint="The account or reference number the vendor prints on their invoices. Enter the existing open PO number here so the vendor does not need to change anything."
            >
              <Input
                id="vendorReference"
                placeholder="e.g. PO-2024-0031"
                value={vendorReference}
                onChange={(e) => setVendorReference(e.target.value)}
              />
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
              <FormField label="Grace Days" htmlFor="graceDays" hint="Days after Valid To an invoice can still settle against this agreement">
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
                <label className="text-sm font-medium text-neutral-700">Currency <span className="text-danger-600">*</span></label>
                <select
                  value={currency}
                  onChange={(e) => {
                    const c = e.target.value as Currency
                    setCurrency(c)
                    if (c !== 'CAD') { setTaxRate(null); setTaxCode(null) }
                  }}
                  className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                >
                  {CURRENCIES.filter((c) => (config?.enabled_currencies ?? ['CAD', 'USD', 'EUR', 'RMB']).includes(c.value)).map((c) => (
                    <option key={c.value} value={c.value}>{c.value} — {c.label}</option>
                  ))}
                </select>
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
                  {taxCodes.map((c) => (
                    <option key={c.code} value={c.code}>
                      {+(Number(c.rate) * 100).toFixed(3)}% — {c.name}
                    </option>
                  ))}
                </select>
              </div>

              {/* Department — drives the Budget Account cascade below it, same
                  as PrCreatePage: pick department first, cascade filters on it. */}
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
            </div>

            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">Budget Account</label>
                <BudgetAccountCascade
                  departmentId={departmentId || undefined}
                  costCenterId={costCenterId}
                  budgetCode={budgetCode}
                  onChange={(next) => { setCostCenterId(next.costCenterId); setBudgetCode(next.budgetCode) }}
                />
                {!departmentId && (
                  <p className="text-xs text-neutral-400">Select a department first to enable the budget account picker.</p>
                )}
              </div>

              {/* Owner */}
              <div ref={ownerAnchorRef} className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">Owner</label>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
                  <input
                    type="text"
                    placeholder="Search people by name…"
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
              </div>
            </div>
          </div>

          {/* Recurring cycle — only for agreement_type === 'recurring' */}
          {agreementType === 'recurring' && (
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-5">
              <h2 className="text-base font-semibold text-neutral-900">Recurring Cycle</h2>
              <RecurringFields value={recurringValue} validFrom={validFrom} onChange={setRecurringValue} />
              {errors.recurring && <p className="text-xs text-danger-600">{errors.recurring}</p>}
            </div>
          )}

          {/* Milestone stages — only for agreement_type === 'milestone' */}
          {agreementType === 'milestone' && (
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-4">
              <h2 className="text-base font-semibold text-neutral-900">Milestone Stages</h2>
              <MilestoneEditor
                rows={milestoneRows}
                notToExceed={notToExceed}
                currency={currency}
                onChange={setMilestoneRows}
              />
              {errors.milestones && <p className="text-xs text-danger-600">{errors.milestones}</p>}
            </div>
          )}

          {/* Notes */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-4">
            <h2 className="text-base font-semibold text-neutral-900">Notes</h2>
            <FormField label="Internal Notes" htmlFor="notes">
              <textarea
                id="notes"
                rows={3}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Terms, special conditions, background…"
                className="w-full resize-none rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
              />
            </FormField>
          </div>

          {/* Attachments */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-4">
            <h2 className="text-base font-semibold text-neutral-900">Attachments</h2>
            <label
              htmlFor="agreement-file-upload"
              className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed border-neutral-300 p-6 text-center hover:border-primary-400 hover:bg-primary-50 transition-colors"
            >
              <Upload className="h-8 w-8 text-neutral-400" />
              <div>
                <p className="text-sm font-medium text-neutral-700">Drag &amp; drop or click to upload</p>
                <p className="text-xs text-neutral-400 mt-1">Contract copies, signed schedules, etc.</p>
              </div>
              <input id="agreement-file-upload" type="file" multiple className="sr-only" onChange={handleFileInput} />
            </label>
            {attachments.map((f, i) => (
              <div key={i} className="flex items-center gap-2 rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm">
                <span className="flex-1 truncate text-neutral-700">📎 {f.name}</span>
                <span className="text-neutral-400 text-xs">{(f.size / 1024 / 1024).toFixed(1)} MB</span>
                <button
                  type="button"
                  onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}
                  className="text-neutral-400 hover:text-danger-600"
                  aria-label="Remove file"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>

          {/* Footer actions */}
          <div className="flex items-center justify-between rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] px-6 py-4">
            <Link to="/agreements">
              <Button variant="ghost">Cancel</Button>
            </Link>
            <div className="flex items-center gap-3">
              <Button
                variant="secondary"
                onClick={() => handleSubmit(false)}
                disabled={isSubmitting}
              >
                Save as Draft
              </Button>
              <Button
                onClick={() => handleSubmit(true)}
                disabled={isSubmitting}
              >
                {isSubmitting ? 'Submitting…' : 'Submit for Approval'}
              </Button>
            </div>
          </div>
        </div>

        {/* Sidebar — Tips */}
        <div className="lg:col-span-3 flex flex-col gap-4">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5 sticky top-6">
            <h3 className="text-sm font-semibold text-neutral-700 mb-3">💡 Tips</h3>
            <ul className="flex flex-col gap-2.5 text-xs text-neutral-500 leading-relaxed">
              <li>• An agreement replaces keeping one PO open indefinitely — use it for house-account spend with no PO and no goods receipt.</li>
              <li>• Every invoice matched to this agreement is flagged as a legacy settlement with a mandatory reason, since there is no receipt evidence yet.</li>
              <li>• Not to Exceed is a warning only — it never blocks matching, approval, or payment.</li>
              <li>• Grace Days lets an invoice dated shortly after Valid To still settle against this agreement.</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  )
}
