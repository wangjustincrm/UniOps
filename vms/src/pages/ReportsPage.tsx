/** Compliance reports (W11+W12 S2-E / PRD §6.5.5 VMS-CR-001..002).
 *
 * Auditor/admin only. Two CSV exports today:
 *   - cfia-visit-log: 16-column per-visit log over a date range
 *   - gmp-area-summary: per-area aggregate counts over a date range
 *
 * F8 locked decision: ship CSV-only (interim format). Excel opens cleanly;
 * we'll swap to the formal CFIA layout when they hand us one.
 */
import { useState } from 'react'
import { FileDown, Loader2, AlertCircle, CheckCircle2 } from 'lucide-react'
import { downloadReport, type ReportKind } from '@/services/api'
import { localToday } from '@/lib/utils'

import { Input, Button } from '@uniops/shell'
interface ReportDef {
  kind: ReportKind
  label: string
  description: string
}

const REPORTS: ReportDef[] = [
  {
    kind: 'cfia-visit-log',
    label: 'CFIA Visit Log',
    description:
      '16-column per-visit log for regulator inspections. Includes visitor, host, area, health-decl, on-site duration, PPE.',
  },
  {
    kind: 'gmp-area-summary',
    label: 'GMP / Lab Area Summary',
    description:
      'One row per restricted area: total visits, health-decl outcomes, after-hours arrivals, unreturned badges.',
  },
]

/** The plant's month so far (company time zone): 1st of this month → today. */
function defaultRange(): { from: string; to: string } {
  const to = localToday()
  return { from: `${to.slice(0, 7)}-01`, to }
}

export default function ReportsPage() {
  const [{ from, to }, setRange] = useState(defaultRange())
  const [busy, setBusy] = useState<ReportKind | null>(null)
  const [lastDownload, setLastDownload] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const onDownload = async (kind: ReportKind) => {
    setError(null)
    setBusy(kind)
    try {
      const filename = await downloadReport(kind, { dateFrom: from, dateTo: to })
      setLastDownload(filename)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Download failed')
    } finally {
      setBusy(null)
    }
  }

  const validRange = !!from && !!to && from <= to

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-semibold text-fg">Compliance Reports</h1>
      <p className="mt-1 text-sm text-fg-muted">
        Export CSV reports for regulator inspections (CFIA) and internal
        compliance review. Every export is recorded in the audit log.
      </p>

      {/* Date range */}
      <div className="mt-5 rounded-lg border border-line bg-surface p-4">
        <p className="text-xs font-semibold text-fg-muted">
          Report period
        </p>
        <div className="mt-2 flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="block text-xs text-fg-muted">From</span>
            <Input
              type="date"
              value={from}
              onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))}
              className="mt-0.5"
            />
          </label>
          <label className="text-sm">
            <span className="block text-xs text-fg-muted">To</span>
            <Input
              type="date"
              value={to}
              onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))}
              className="mt-0.5"
            />
          </label>
          {!validRange && (
            <p className="text-xs text-danger-fg">
              ‘From’ must be on or before ‘To’.
            </p>
          )}
        </div>
      </div>

      {/* Feedback */}
      {error && (
        <p className="mt-3 flex items-center gap-1.5 rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-xs text-danger-fg">
          <AlertCircle className="h-3.5 w-3.5" />
          {error}
        </p>
      )}
      {lastDownload && !error && (
        <p className="mt-3 flex items-center gap-1.5 rounded-md border border-success-line bg-success-soft px-3 py-2 text-xs text-success-fg">
          <CheckCircle2 className="h-3.5 w-3.5" />
          Downloaded {lastDownload}
        </p>
      )}

      {/* Reports list */}
      <div className="mt-5 space-y-3">
        {REPORTS.map((r) => (
          <div
            key={r.kind}
            className="flex items-start justify-between gap-4 rounded-lg border border-line bg-surface p-4"
          >
            <div className="min-w-0">
              <p className="text-sm font-semibold text-fg">{r.label}</p>
              <p className="mt-0.5 text-xs text-fg-muted">{r.description}</p>
            </div>
            <Button size="sm"
              type="button"
              onClick={() => onDownload(r.kind)}
              disabled={!validRange || busy === r.kind}
              className="shrink-0"
            >
              {busy === r.kind
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <FileDown className="h-4 w-4" />}
              Download CSV
            </Button>
          </div>
        ))}
      </div>

      <p className="mt-5 text-xs text-fg-muted">
        CSV format is current interim layout (Excel opens these cleanly).
        Once CFIA confirms a formal template, the column order may change.
      </p>
    </div>
  )
}
