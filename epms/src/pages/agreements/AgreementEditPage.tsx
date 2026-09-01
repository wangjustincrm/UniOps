import { useEffect, useRef, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { BackLink, useDocTabTitle } from '@/components/BackLink'
import { useReplaceTab } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'
import { ArrowLeft, Search, Upload, X } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FormField } from '@/components/ui/form-field'
import { DropdownPortal, useAnchorRect } from '@/components/ui/DropdownPortal'
import { cn } from '@/lib/utils'
import { useAgreement, useAgreementSchedule, useUpdateAgreement } from '@/hooks/useAgreements'
import { agreementService, type AgreementType, type MilestoneRowIn, type UpdateAgreementBody } from '@/services/agreement'
import { agreementAttachmentService } from '@/services/agreementAttachments'
import { useDepartments } from '@/hooks/useDepartments'
import { useTaxCodes } from '@/hooks/useTaxCodes'
import { userService, type ApiUserBrief } from '@/services/users'
import { BudgetAccountCascade } from '@/components/agreements/BudgetAccountCascade'
import { RecurringFields, EMPTY_RECURRING_FIELDS, type RecurringFieldsValue } from '@/components/agreements/RecurringFields'
import { MilestoneEditor } from '@/components/agreements/MilestoneEditor'

// Matches epms-api/app/crud/agreement.py EDITABLE_STATUSES = ("draft", "returned").
const EDITABLE_STATUSES = ['draft', 'returned']

const TYPE_LABELS: Record<AgreementType, string> = {
  house_account: 'House Account',
  recurring: 'Recurring',
  milestone: 'Milestone',
}

export default function AgreementEditPage() {
  const { id } = useParams<{ id: string }>()
  const replaceTab = useReplaceTab(epmsRoutes)
  const { data: agreement, isLoading } = useAgreement(id ?? '')
  useDocTabTitle(agreement?.number && `Edit ${agreement.number}`)
  const updateAgreement = useUpdateAgreement()
  const { data: deptData } = useDepartments()
  const departments = (deptData?.items ?? []).filter((d) => d.is_active)
  const taxCodes = useTaxCodes()
  const {
    data: scheduleData,
    isError: scheduleErrored,
    refetch: refetchSchedule,
  } = useAgreementSchedule(id ?? '')

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
  const [costCenterId, setCostCenterId] = useState<string | undefined>(undefined)
  const [budgetCode, setBudgetCode] = useState('')
  const [notes, setNotes] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Recurring cycle fields — hydrated from the loaded agreement below. Type
  // is locked post-creation, so no "clear the other type's state" gate is
  // needed here the way AgreementCreatePage needs one.
  const [recurringValue, setRecurringValue] = useState<RecurringFieldsValue>(EMPTY_RECURRING_FIELDS)

  // Milestone stages — hydrated from the schedule endpoint, not from
  // `agreement` itself (stages live in agreement_payment_schedule and are
  // written immediately on create/update, independent of approval status —
  // see crud/agreement.py::create/update calling replace_milestone_rows).
  //
  // `milestonesHydrated` is state, not a ref, for two reasons that both
  // matter (review finding 1 — this used to be a ref, which silently deleted
  // every stage): it has to (a) re-render so the Save buttons + the stages
  // card can visibly gate on it, since `agreement.agreement_type ===
  // 'milestone'` goes true from the FIRST query (useAgreement) while
  // `milestoneRows` is still `[]` from the SECOND, independent query
  // (useAgreementSchedule) — the ordinary path into this page (Detail page's
  // Edit link) warms neither query in advance, so that gap is real, not
  // theoretical; and (b) be readable by handleSave to decide whether
  // `milestones` belongs in the PATCH body at all — omitted means "leave
  // stages alone" (safe default before we know what they are), `[]` means
  // "the user emptied every row" (must only be sent once rows are known to
  // be the real ones, not the not-yet-loaded placeholder).
  const [milestoneRows, setMilestoneRows] = useState<MilestoneRowIn[]>([])
  const [milestonesHydrated, setMilestonesHydrated] = useState(false)

  // Attachments — new files queued locally, uploaded after a successful save,
  // same sequencing as AgreementCreatePage / PrCreatePage.tsx:335.
  const [attachments, setAttachments] = useState<File[]>([])
  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    setAttachments((prev) => [...prev, ...files])
    e.target.value = ''
  }

  // Owner picker
  const ownerAnchorRef = useRef<HTMLDivElement>(null)
  const [ownerOpen, setOwnerOpen] = useState(false)
  const ownerRect = useAnchorRect(ownerOpen, ownerAnchorRef)
  const [ownerQuery, setOwnerQuery] = useState('')
  const [selectedOwner, setSelectedOwner] = useState<ApiUserBrief | null>(null)
  // True only once the user has actively interacted with the picker (picked
  // someone, or hit the clear button) — see the pre-fill effect and
  // handleSave below for why this is tracked separately from selectedOwner
  // being non-null.
  const [ownerTouched, setOwnerTouched] = useState(false)
  const { data: ownersData } = useQuery({
    queryKey: ['users', 'directory', ownerQuery],
    queryFn: () => userService.directory({ search: ownerQuery || undefined }),
    enabled: ownerOpen,
    staleTime: 30_000,
  })
  const owners = ownersData?.items ?? []

  // Resolve the EXISTING owner_id into a display name via GET
  // /users/directory/{id} — open to any authenticated user, unlike GET
  // /users/{id} (system_admin-only). See the pre-fill effect below for how
  // this populates selectedOwner without marking the field "touched".
  const { data: resolvedOwner } = useQuery({
    queryKey: ['users', 'directory', agreement?.owner_id],
    queryFn: () => userService.directoryGet(agreement!.owner_id!),
    enabled: !!agreement?.owner_id,
  })

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
    setCostCenterId(agreement.cost_center_id ?? undefined)
    setBudgetCode(agreement.budget_code ?? '')
    setNotes(agreement.notes ?? '')
    setRecurringValue({
      recurringType: agreement.recurring_type ?? '',
      expectedInvoiceDay: agreement.expected_invoice_day != null ? String(agreement.expected_invoice_day) : '',
      anchorMonth: agreement.anchor_month != null ? String(agreement.anchor_month) : '',
      scheduleStartDate: agreement.schedule_start_date ?? '',
      amountPerPeriod: agreement.expected_amount_per_period ?? '',
      tolerancePct: agreement.tolerance_pct ?? '',
      overdueAfterDays: agreement.overdue_after_days != null ? String(agreement.overdue_after_days) : '7',
    })
    // owner_id itself is resolved into the picker by the effect below, via
    // GET /users/directory/{id} — that endpoint IS the non-admin-safe
    // id-to-name lookup (AgreementDetailPage's Confirmed column uses the same
    // /users/directory family already). Reset here so a stale owner from a
    // PREVIOUS agreement doesn't linger while the resolution query is still
    // in flight for this one.
    setSelectedOwner(null)
    setOwnerTouched(false)
  }, [agreement?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Populate the picker with the CURRENT owner's name once resolved.
  // Deliberately does NOT set ownerTouched — handleSave only sends owner_id
  // when the user has actively interacted with the picker (searched and
  // picked someone, or hit clear). Before this effect existed, selectedOwner
  // stayed null on load and handleSave's `owner_id: selectedOwner?.id ||
  // null` sent an explicit `null` on EVERY save regardless — since PATCH
  // uses model_dump(exclude_unset=True), an explicit null in the request
  // body counts as "set", so every edit-and-save silently wiped a
  // previously-set owner unless the user happened to re-pick them.
  useEffect(() => {
    if (resolvedOwner) setSelectedOwner(resolvedOwner)
  }, [resolvedOwner])

  // Milestone stages come from the schedule endpoint, not `agreement` itself
  // (see the comment on milestonesHydrated above). Hydrate once per agreement
  // id, guarded so a background refetch doesn't stomp in-progress edits with
  // the last-saved rows.
  useEffect(() => {
    setMilestonesHydrated(false)
  }, [agreement?.id])

  useEffect(() => {
    if (!agreement || agreement.agreement_type !== 'milestone') return
    if (!scheduleData || milestonesHydrated) return
    setMilestonesHydrated(true)
    setMilestoneRows(
      scheduleData.items
        .filter((r) => r.schedule_type === 'milestone')
        .map((r) => ({
          milestone_name: r.milestone_name ?? '',
          expected_timing: r.expected_timing,
          expected_amount: r.expected_amount,
          amount_pct: r.amount_pct,
        }))
    )
  }, [agreement, scheduleData, milestonesHydrated])

  // Three-way state for a milestone agreement's stage rows, per review round
  // 2 finding 1: the FIRST fix blocked Save on `!milestonesHydrated` alone,
  // which is correct while the schedule query is genuinely in flight but
  // WRONG if it fails outright — a persistently-failing GET would leave
  // `milestonesHydrated` false forever, permanently disabling Save (even for
  // a title-only edit) with no error and no way out. The three states have
  // to be told apart:
  //   - loading  → Save blocked, "Loading stages…" (unchanged from round 1)
  //   - errored  → Save ALLOWED. Omitting `milestones` from the PATCH body
  //                (handled below in handleSave, gated on milestonesHydrated
  //                specifically, not on "not still loading") means "leave
  //                stages alone" — exactly correct when they couldn't be
  //                read, and strictly safer than either guessing at their
  //                content or bricking the rest of the form over it. Shown
  //                with an inline error + Retry, not silently treated as "no
  //                stages".
  //   - loaded   → normal MilestoneEditor, Save allowed.
  const milestonesLoading =
    !!agreement && agreement.agreement_type === 'milestone' && !milestonesHydrated && !scheduleErrored
  const milestonesErrored =
    !!agreement && agreement.agreement_type === 'milestone' && !milestonesHydrated && scheduleErrored

  const validate = (): boolean => {
    const e: Record<string, string> = {}
    if (!title.trim()) e.title = 'Title is required'
    // Owner is required to leave draft (epms-api rejects a submit without one)
    // and can never be edited afterwards, so it is enforced on Save too rather
    // than only on Submit. Read through ownerTouched, not selectedOwner alone:
    // an untouched picker is still null for the moment it takes the directory
    // lookup to resolve the EXISTING owner_id into a name (see the effect
    // above), and treating that window as "no owner" would block Save on a
    // form the user never touched.
    if (!(ownerTouched ? !!selectedOwner : !!agreement?.owner_id)) {
      e.owner = 'Please select an owner'
    }
    if (!validFrom) e.validFrom = 'Valid From date is required'
    if (!validTo) e.validTo = 'Valid To date is required'
    if (validFrom && validTo && validTo < validFrom) e.validTo = 'Valid To must be on or after Valid From'
    if (agreement?.agreement_type === 'recurring') {
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
    if (agreement?.agreement_type === 'milestone' && milestoneRows.some((r) => !r.milestone_name.trim())) {
      e.milestones = 'Every stage needs a name'
    }
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSave = async (andSubmit: boolean) => {
    // Defense in depth alongside the button's `disabled={milestonesLoading}`
    // below: even if this is reached some other way, refuse to build a
    // `milestones` payload from rows that are still the pre-hydration `[]`
    // placeholder — that would read as "the user deleted every stage" and
    // `replace_milestone_rows` would honor it (review finding 1). Note this
    // only blocks the LOADING window, not the errored one — a schedule fetch
    // that failed outright must not also disable saving everything else on
    // the form (round 2 finding 1); the `milestones` key is simply left out
    // of the body below in that case (gated on `milestonesHydrated`).
    if (milestonesLoading) return
    if (!validate()) return
    setIsSubmitting(true)
    try {
      // Cleared fields must go out as explicit `null`, NOT `undefined`.
      // JSON.stringify drops undefined keys, and the backend uses
      // model_dump(exclude_unset=True) — so an undefined here means "don't
      // touch", and clearing Not-to-Exceed / tax code / department appeared
      // to save while keeping the old value. All these columns are nullable.
      // PoEditPage sends null for the same reason. owner_id is the one
      // exception to "always explicit null" — see its own comment below.
      const body: UpdateAgreementBody = {
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
        // Only sent when the user actually interacted with the picker
        // (ownerTouched) — see the effect that resolves the existing owner
        // into selectedOwner above. `undefined` here is dropped by
        // JSON.stringify, so an untouched picker leaves owner_id out of the
        // body entirely — true "no change" via exclude_unset, matching the
        // pattern documented for the other nullable columns just above.
        owner_id: ownerTouched ? (selectedOwner?.id || null) : undefined,
        notes: notes || null,
        cost_center_id: costCenterId ?? null,
      }
      // agreement_type is locked post-creation (no field on UpdateAgreementBody),
      // so only the ALREADY-chosen type's fields are ever populated here — no
      // "switch type, clear the other side" gate is needed the way the create
      // page needs one.
      if (agreement?.agreement_type === 'recurring') {
        body.recurring_type = recurringValue.recurringType || null
        body.expected_invoice_day = recurringValue.expectedInvoiceDay ? Number(recurringValue.expectedInvoiceDay) : null
        body.anchor_month = recurringValue.anchorMonth ? Number(recurringValue.anchorMonth) : null
        body.schedule_start_date = recurringValue.scheduleStartDate || null
        body.expected_amount_per_period = recurringValue.amountPerPeriod ? Number(recurringValue.amountPerPeriod) : null
        body.tolerance_pct = recurringValue.tolerancePct ? Number(recurringValue.tolerancePct) : null
        body.overdue_after_days = recurringValue.overdueAfterDays ? Number(recurringValue.overdueAfterDays) : null
      } else if (agreement?.agreement_type === 'milestone' && milestonesHydrated) {
        // Always resent on save — this is the round-trip that lets an empty
        // `rows` array explicitly clear all stages (AgreementUpdate.milestones:
        // None = leave alone, [] = clear — see schemas/agreement.py). Gated on
        // `milestonesHydrated`, not just the type, so this branch can only run
        // once `milestoneRows` is known to be the REAL stage list — covering
        // both the loading window (blocked earlier by `milestonesLoading`)
        // and the errored one (Save is allowed to proceed, but `milestones`
        // is simply omitted here, which is "leave stages alone" — the only
        // safe thing to do when they couldn't be read).
        body.milestones = milestoneRows
          .filter((r) => r.milestone_name.trim())
          .map((r) => ({
            milestone_name: r.milestone_name.trim(),
            expected_timing: r.expected_timing?.trim() || null,
            expected_amount: r.expected_amount || null,
            amount_pct: notToExceed ? (r.amount_pct || null) : null,
          }))
      }

      await updateAgreement.mutateAsync({ id: id!, body })
      if (attachments.length > 0) {
        // The save already succeeded at this point — an upload failure here
        // must not read as "nothing happened". Catch it, tell the user
        // explicitly which half succeeded, and keep going (submit action +
        // navigate) rather than leaving them on a form for changes that were,
        // in fact, already saved.
        try {
          await Promise.all(attachments.map((f) => agreementAttachmentService.upload(id!, f)))
        } catch {
          alert(
            `${agreement?.number ?? 'The agreement'} was saved, but one or more attachments failed to upload. ` +
            'You can add them again from the agreement page.'
          )
        }
      }
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

  if (!agreement || !EDITABLE_STATUSES.includes(agreement.status)) {
    return (
      <div className="p-8">
        <p className="text-sm text-neutral-500">This agreement cannot be edited in its current status.</p>
        <BackLink to={`/agreements/${id}`} className="mt-3 inline-block text-sm text-primary-600 hover:underline">← Back to Agreement</BackLink>
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
                <label className="text-sm font-medium text-neutral-700">
                  Owner <span className="text-danger-600">*</span>
                </label>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
                  <input
                    type="text"
                    placeholder={agreement.owner_id && !selectedOwner ? 'Search to change owner…' : 'Search people by name…'}
                    value={selectedOwner ? selectedOwner.full_name : ownerQuery}
                    onFocus={() => { setOwnerOpen(true); if (selectedOwner) setOwnerQuery('') }}
                    onChange={(e) => { setOwnerQuery(e.target.value); setSelectedOwner(null); setOwnerOpen(true); setErrors((p) => ({ ...p, owner: '' })) }}
                    className={cn(
                      'h-10 w-full rounded-md border bg-white pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
                      errors.owner ? 'border-danger-600' : 'border-neutral-300'
                    )}
                  />
                  {selectedOwner && (
                    <button
                      type="button"
                      onClick={() => { setSelectedOwner(null); setOwnerTouched(true); setOwnerQuery('') }}
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
                        onClick={() => { setSelectedOwner(u); setOwnerTouched(true); setOwnerOpen(false); setOwnerQuery(''); setErrors((p) => ({ ...p, owner: '' })) }}
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
                {agreement.owner_id && !selectedOwner && !ownerTouched && (
                  <p className="text-xs text-neutral-400">
                    An owner is already set. Leave blank to keep it, or search to replace it.
                  </p>
                )}
                {errors.owner && <p className="text-xs text-danger-600">{errors.owner}</p>}
              </div>
            </div>
          </div>

          {/* Recurring cycle — agreement_type is locked post-creation, so this
              renders only when the loaded agreement already is 'recurring'. */}
          {agreement.agreement_type === 'recurring' && (
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-5">
              <h2 className="text-base font-semibold text-neutral-900">Recurring Cycle</h2>
              <RecurringFields value={recurringValue} validFrom={validFrom} onChange={setRecurringValue} />
              {errors.recurring && <p className="text-xs text-danger-600">{errors.recurring}</p>}
            </div>
          )}

          {/* Milestone stages — same lock as above, keyed off the existing type. */}
          {agreement.agreement_type === 'milestone' && (
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-4">
              <h2 className="text-base font-semibold text-neutral-900">Milestone Stages</h2>
              {milestonesLoading ? (
                // Not "no stages" — the schedule query hasn't resolved yet.
                // Rendering MilestoneEditor with rows=[] here would look
                // identical to a genuinely empty stage list, which is exactly
                // the ambiguity that let Save silently wipe stages before
                // this query settled (review finding 1). Show a distinct
                // loading state instead, and Save stays disabled below until
                // this clears.
                <p className="text-sm text-neutral-400">Loading stages…</p>
              ) : milestonesErrored ? (
                // Round 2 finding 1: a persistently failing schedule fetch
                // must not read as "there are no stages", and must not brick
                // the rest of the form either — Save stays available below
                // (handleSave simply omits `milestones` from the PATCH,
                // which is "leave stages alone", the safe default when they
                // couldn't be read at all).
                <div className="flex flex-col items-start gap-2 rounded-lg border border-warning-200 bg-warning-50 px-4 py-3">
                  <p className="text-sm text-warning-700">
                    Could not load the stage schedule. Saving now will leave the existing stages unchanged.
                  </p>
                  <Button type="button" variant="secondary" size="sm" onClick={() => refetchSchedule()}>
                    Retry
                  </Button>
                </div>
              ) : (
                <MilestoneEditor
                  rows={milestoneRows}
                  notToExceed={notToExceed}
                  currency={agreement.currency}
                  onChange={setMilestoneRows}
                />
              )}
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
            <Link to={`/agreements/${id}`}>
              <Button variant="ghost">Cancel</Button>
            </Link>
            <div className="flex items-center gap-3">
              {milestonesLoading && (
                <p className="text-xs text-neutral-400">Loading stages before Save can be used…</p>
              )}
              <Button
                variant="secondary"
                onClick={() => handleSave(false)}
                disabled={isSubmitting || milestonesLoading}
              >
                Save Draft
              </Button>
              <Button onClick={() => handleSave(true)} disabled={isSubmitting || milestonesLoading}>
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
