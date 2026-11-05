/** Small shared pieces for the Freight pages (badges, modal, selects, dates). */
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { ApiCostCenter } from '@/services/cost_centers'
import type { ApiDepartment } from '@/services/departments'
import type { MatchStatus } from '@/services/freight'

export const SELECT_CLS =
  'h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-50 disabled:text-neutral-400'
export const SELECT_SM_CLS =
  'h-8 rounded-md border border-neutral-300 bg-white px-2 text-xs text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-50 disabled:text-neutral-400'
export const INPUT_SM_CLS =
  'h-8 rounded-md border border-neutral-300 bg-white px-2 text-xs text-neutral-800 focus:outline-none focus:ring-2 focus:ring-primary-600'
export const TH_CLS = 'px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500 whitespace-nowrap'

// ─── Badges ───────────────────────────────────────────────────────────────────

type Variant = 'neutral' | 'warning' | 'info' | 'success' | 'danger' | 'dark'

function Dot({ cls }: { cls: string }) {
  return <span className={cn('size-1.5 rounded-full', cls)} />
}

const SHIPMENT_STATUS: Record<string, { label: string; variant: Variant; dot: string }> = {
  pending: { label: 'Pending', variant: 'warning', dot: 'bg-warning-500' },
  billed: { label: 'Billed', variant: 'success', dot: 'bg-success-600' },
  void: { label: 'Void', variant: 'dark', dot: 'bg-neutral-400' },
}

export function ShipmentStatusBadge({ status }: { status: string }) {
  const c = SHIPMENT_STATUS[status] ?? { label: status, variant: 'neutral' as const, dot: 'bg-neutral-400' }
  return <Badge variant={c.variant}><Dot cls={c.dot} />{c.label}</Badge>
}

const INVOICE_STATUS: Record<string, { label: string; variant: Variant; dot: string }> = {
  unmatched: { label: 'Matching', variant: 'warning', dot: 'bg-warning-500' },
  matched: { label: 'Matched', variant: 'success', dot: 'bg-success-600' },
  paid: { label: 'Paid', variant: 'dark', dot: 'bg-neutral-400' },
}

export function FreightInvoiceStatusBadge({ status }: { status: string }) {
  const c = INVOICE_STATUS[status] ?? { label: status, variant: 'neutral' as const, dot: 'bg-neutral-400' }
  return <Badge variant={c.variant}><Dot cls={c.dot} />{c.label}</Badge>
}

/** Marks a courier invoice (match_route='freight') in the general invoice lists. */
export function FreightRouteBadge({ className }: { className?: string }) {
  return (
    <span className={cn('inline-flex items-center rounded-full border border-primary-200 bg-primary-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary-700', className)}>
      Freight
    </span>
  )
}

/** Marks an invoice matched for a Direct PA (match_route='direct') in the lists. */
export function DirectRouteBadge({ className }: { className?: string }) {
  return (
    <span className={cn('inline-flex items-center rounded-full border border-warning-200 bg-warning-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-warning-700', className)}>
      Direct
    </span>
  )
}

export function MatchBadge({ status, previouslyBilledOn }: { status: MatchStatus | string; previouslyBilledOn?: string | null }) {
  switch (status) {
    case 'auto':
      return <Badge variant="success">Matched</Badge>
    case 'manual':
      return <Badge variant="info">Set by hand</Badge>
    case 'default':
      return <Badge variant="neutral">Carrier default</Badge>
    case 'unregistered':
      return <Badge variant="warning">Not registered</Badge>
    case 'duplicate_billing':
      return (
        <Badge variant="danger">
          {previouslyBilledOn ? `Billed before on ${previouslyBilledOn}` : 'Billed before'}
        </Badge>
      )
    case 'unmatched':
      return <Badge variant="warning">Needs a cost center</Badge>
    default:
      return <Badge variant="neutral">{status}</Badge>
  }
}

export const CHARGE_TYPE_LABEL: Record<string, string> = {
  shipment: 'Shipment',
  pickup: 'Pickup',
  fee: 'Fee',
  adjustment: 'Adjustment',
}

// ─── Modal (portal, same look as the PA approval dialog) ─────────────────────

export function Modal({ title, subtitle, onClose, children, wide = false }: {
  title: string; subtitle?: string; onClose: () => void; children: React.ReactNode; wide?: boolean
}) {
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
      <div className={cn('flex max-h-[92vh] w-full flex-col rounded-2xl bg-white shadow-2xl', wide ? 'max-w-6xl' : 'max-w-lg')}>
        <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4">
          <div>
            <h2 className="text-sm font-semibold text-neutral-900">{title}</h2>
            {subtitle && <p className="text-xs text-neutral-500">{subtitle}</p>}
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="overflow-y-auto px-6 py-5">{children}</div>
      </div>
    </div>,
    document.body,
  )
}

// ─── Cost-center select (grouped by department) ─────────────────────────────

export function CostCenterSelect({
  value, onChange, costCenters, departments, departmentId, disabled, className, placeholder = 'Select cost center…',
}: {
  value: string
  onChange: (id: string) => void
  costCenters: ApiCostCenter[]
  departments: ApiDepartment[]
  /** Restrict to one department (no grouping then). */
  departmentId?: string
  disabled?: boolean
  className?: string
  placeholder?: string
}) {
  const active = costCenters.filter((c) => c.is_active || c.id === value)
  const label = (c: ApiCostCenter) => `${c.code} — ${c.name}`
  if (departmentId) {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} className={className ?? SELECT_CLS}>
        <option value="">{placeholder}</option>
        {active.filter((c) => c.department_id === departmentId).map((c) => (
          <option key={c.id} value={c.id}>{label(c)}</option>
        ))}
      </select>
    )
  }
  const deptName = new Map(departments.map((d) => [d.id, d.name]))
  const groups = new Map<string, ApiCostCenter[]>()
  for (const c of active) {
    const k = deptName.get(c.department_id) ?? 'Other'
    groups.set(k, [...(groups.get(k) ?? []), c])
  }
  const sorted = [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} className={className ?? SELECT_CLS}>
      <option value="">{placeholder}</option>
      {sorted.map(([dept, ccs]) => (
        <optgroup key={dept} label={dept}>
          {ccs.map((c) => <option key={c.id} value={c.id}>{label(c)}</option>)}
        </optgroup>
      ))}
    </select>
  )
}

// ─── Dates (date-only strings never go through new Date(string)) ────────────

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/

/** "2026-10-19" + 30 → "2026-11-18", computed on the calendar, not in UTC. */
export function addDaysISO(dateOnly: string, days: number): string {
  const m = DATE_ONLY.exec(dateOnly)
  if (!m) return ''
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + days)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function isDateOnly(v: string | null | undefined): v is string {
  return !!v && DATE_ONLY.test(v)
}

/** Whole days since a real timestamp (created_at is a datetime, not a date). */
export function daysSince(timestamp: string): number {
  const t = new Date(timestamp).getTime()
  if (isNaN(t)) return 0
  return Math.floor((Date.now() - t) / 86_400_000)
}

/** Money as a number, tolerant of the Decimal-as-string wire format. */
export const num = (v: string | number | null | undefined): number => {
  const n = Number(v ?? 0)
  return isNaN(n) ? 0 : n
}

/** Round to cents to avoid 0.1 + 0.2 noise in the live checks. */
export const cents = (n: number): number => Math.round(n * 100) / 100

export function errorMessage(err: unknown, fallback = 'Something went wrong'): string {
  return err instanceof Error ? err.message : fallback
}
