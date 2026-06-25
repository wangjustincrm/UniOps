import { Link } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { useDashboard } from '@/hooks/useDashboard'

function StatusCountList({ counts }: { counts: [string, number][] }) {
  return (
    <div className="flex flex-col gap-1.5 mt-3">
      {counts.map(([status, count]) => (
        <div key={status} className="flex justify-between text-sm">
          <span className="capitalize text-neutral-600">{status.replace(/_/g, ' ')}</span>
          <span className="font-mono font-semibold text-neutral-900">{count}</span>
        </div>
      ))}
    </div>
  )
}

export default function AuditorDashboard() {
  const { data } = useDashboard()

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  const breakdown = data?.status_breakdown

  const toSortedCounts = (map?: Record<string, number>): [string, number][] =>
    Object.entries(map ?? {}).sort(([a], [b]) => a.localeCompare(b))

  const prCounts = toSortedCounts(breakdown?.pr)
  const poCounts = toSortedCounts(breakdown?.po)
  const grCounts = toSortedCounts(breakdown?.gr)
  const paCounts = toSortedCounts(breakdown?.pa)

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-neutral-900">Auditor Dashboard</h1>
      <p className="text-sm text-neutral-500">Read-only overview of all procurement documents and statuses.</p>

      {/* Stats bar */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard title="Total PRs" value={kpi('Total PRs')?.value ?? '—'} subtitle="Purchase requisitions" />
        <StatCard title="Total POs" value={kpi('Total POs')?.value ?? '—'} subtitle="Purchase orders" />
        <StatCard title="Total Invoices" value={kpi('Total Invoices')?.value ?? '—'} subtitle="All invoices" />
        <StatCard title="Total PAs" value={kpi('Total PAs')?.value ?? '—'} subtitle="Payment applications" />
      </div>

      {/* Status breakdown cards */}
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-xl bg-white shadow p-5">
          <h2 className="text-base font-semibold text-neutral-900">Purchase Requisitions</h2>
          <StatusCountList counts={prCounts} />
          <Link to="/pr" className="mt-4 block text-xs text-primary-600 hover:underline">View all PRs →</Link>
        </div>
        <div className="rounded-xl bg-white shadow p-5">
          <h2 className="text-base font-semibold text-neutral-900">Purchase Orders</h2>
          <StatusCountList counts={poCounts} />
          <Link to="/po" className="mt-4 block text-xs text-primary-600 hover:underline">View all POs →</Link>
        </div>
        <div className="rounded-xl bg-white shadow p-5">
          <h2 className="text-base font-semibold text-neutral-900">Goods Receipts</h2>
          <StatusCountList counts={grCounts} />
          <Link to="/gr" className="mt-4 block text-xs text-primary-600 hover:underline">View all GRs →</Link>
        </div>
        <div className="rounded-xl bg-white shadow p-5">
          <h2 className="text-base font-semibold text-neutral-900">Payment Applications</h2>
          <StatusCountList counts={paCounts} />
          <Link to="/pa" className="mt-4 block text-xs text-primary-600 hover:underline">View all PAs →</Link>
        </div>
      </div>
    </div>
  )
}
