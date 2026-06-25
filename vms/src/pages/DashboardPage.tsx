/** VMS home dashboard (PRD §6.5.5 / §2.5.2 VMS-AU-016). */
import { Link } from 'react-router-dom'
import {
  UserCheck, CalendarCheck, CalendarRange, AlertTriangle, ArrowRight,
  ShieldCheck, FileWarning, MoonStar, Inbox,
} from 'lucide-react'
import {
  useComplianceMetrics, useDashboardOverview, useMyVmsTasks, useVisits,
  type Visit,
} from '@/services/api'
import { StatusBadge, AccessAreaBadge } from '@/components/StatusBadge'
import { formatDateTime } from '@/lib/utils'

export default function DashboardPage() {
  const overview = useDashboardOverview()
  const compliance = useComplianceMetrics()
  const myTasks = useMyVmsTasks()
  const today = new Date().toISOString().slice(0, 10)
  const todayVisits = useVisits({
    date_from: today, date_to: today, page_size: 50,
  })

  return (
    <div>
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Dashboard</h1>
          <p className="mt-1 text-sm text-neutral-500">
            {new Date().toLocaleDateString('en-CA', {
              weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
            })}
          </p>
        </div>
        <Link
          to="/new"
          className="rounded-md bg-primary-600 px-3 py-2 text-sm font-medium text-white hover:bg-primary-700"
        >
          + New visit
        </Link>
      </div>

      {/* Pending-approvals nudge — only when I have visit tasks waiting on me. */}
      {!myTasks.isLoading && (myTasks.data?.length ?? 0) > 0 && (
        <Link
          to="/tasks"
          className="mt-4 flex items-center justify-between gap-3 rounded-lg border border-primary-200 bg-primary-50/60 px-4 py-3 transition-shadow hover:shadow-md"
        >
          <div className="flex items-center gap-2">
            <span className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-primary-100 text-primary-700">
              <Inbox className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-primary-700">
                {myTasks.data?.length} visit{myTasks.data?.length === 1 ? '' : 's'} waiting on your approval
              </p>
              <p className="text-xs text-primary-700/80">Review and approve, return, or reject.</p>
            </div>
          </div>
          <ArrowRight className="h-4 w-4 text-primary-700/60" />
        </Link>
      )}

      {/* Cards */}
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card
          icon={<UserCheck className="h-5 w-5" />}
          label="On-site now"
          value={overview.data?.on_site_count}
          loading={overview.isLoading}
          accent="bg-emerald-50 text-success-600"
          link="/active"
        />
        <Card
          icon={<CalendarCheck className="h-5 w-5" />}
          label="Today’s visits"
          value={overview.data?.today_count}
          loading={overview.isLoading}
          accent="bg-primary-50 text-primary-700"
          link="/"
        />
        <Card
          icon={<CalendarRange className="h-5 w-5" />}
          label="Past 7 days"
          value={overview.data?.week_count}
          loading={overview.isLoading}
          accent="bg-neutral-100 text-neutral-700"
          link="/all"
        />
        <Card
          icon={<AlertTriangle className="h-5 w-5" />}
          label="Overdue"
          value={overview.data?.overdue_count}
          loading={overview.isLoading}
          accent="bg-amber-50 text-amber-700"
          highlight={!!overview.data?.overdue_count}
        />
      </div>

      {/* Compliance metrics (PRD §6.5.5) */}
      <div className="mt-6">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-base font-semibold text-neutral-900">
            Compliance — this month
          </h2>
          <Link
            to="/reports"
            className="inline-flex items-center gap-1 text-xs text-primary-700 hover:underline"
          >
            Reports
            <ArrowRight className="h-3 w-3" />
          </Link>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Card
            icon={<ShieldCheck className="h-5 w-5" />}
            label="GMP/Lab visits"
            value={compliance.data?.gmp_visits_this_month}
            loading={compliance.isLoading}
            accent="bg-primary-50 text-primary-700"
          />
          <Card
            icon={<ShieldCheck className="h-5 w-5" />}
            label="Health pass rate"
            value={
              compliance.data?.gmp_pass_rate == null
                ? undefined
                : Math.round(compliance.data.gmp_pass_rate * 100)
            }
            suffix={compliance.data?.gmp_pass_rate == null ? undefined : '%'}
            fallback="n/a"
            loading={compliance.isLoading}
            accent="bg-emerald-50 text-success-600"
          />
          <Card
            icon={<FileWarning className="h-5 w-5" />}
            label="Unreturned badges"
            value={compliance.data?.unreturned_badges}
            loading={compliance.isLoading}
            accent="bg-amber-50 text-amber-700"
            highlight={!!compliance.data?.unreturned_badges}
          />
          <Card
            icon={<MoonStar className="h-5 w-5" />}
            label="After-hours today"
            value={compliance.data?.after_hours_visits_today}
            loading={compliance.isLoading}
            accent="bg-neutral-100 text-neutral-700"
          />
        </div>
      </div>

      {/* Today's table */}
      <div className="mt-8">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-base font-semibold text-neutral-900">Today’s appointments</h2>
          <Link to="/all" className="inline-flex items-center gap-1 text-xs text-primary-700 hover:underline">
            All visits
            <ArrowRight className="h-3 w-3" />
          </Link>
        </div>

        <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
          <table className="min-w-full divide-y divide-neutral-200">
            <thead className="bg-neutral-50 text-left text-xs font-semibold uppercase tracking-wider text-neutral-500">
              <tr>
                <th className="px-4 py-2.5">Visit</th>
                <th className="px-4 py-2.5 hidden md:table-cell">Planned arrival</th>
                <th className="px-4 py-2.5">Area</th>
                <th className="px-4 py-2.5">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100 bg-white text-sm">
              {todayVisits.isLoading && (
                <tr><td colSpan={4} className="px-4 py-6 text-center text-neutral-400">Loading…</td></tr>
              )}
              {!todayVisits.isLoading && (todayVisits.data?.items.length ?? 0) === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-12 text-center text-neutral-400">
                    No visits scheduled for today.
                  </td>
                </tr>
              )}
              {todayVisits.data?.items.map((v: Visit) => (
                <tr key={v.id} className="hover:bg-primary-50/30">
                  <td className="px-4 py-2.5">
                    <Link to={`/${v.id}`} className="text-primary-700 hover:underline">
                      {v.visitor
                        ? `${v.visitor.first_name} ${v.visitor.last_name}`
                        : `Visit #${v.id.slice(0, 8)}`}
                    </Link>
                    {v.visitor?.company_name && (
                      <p className="text-xs text-neutral-500">{v.visitor.company_name}</p>
                    )}
                  </td>
                  <td className="px-4 py-2.5 hidden md:table-cell text-neutral-600">
                    {formatDateTime(v.planned_arrival)}
                  </td>
                  <td className="px-4 py-2.5">
                    <AccessAreaBadge area={v.access_area} />
                  </td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={v.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ── Bits ────────────────────────────────────────────────────────────────────-

function Card({
  icon, label, value, loading, accent, link, highlight, suffix, fallback,
}: {
  icon: React.ReactNode
  label: string
  value: number | undefined
  loading: boolean
  accent: string
  link?: string
  highlight?: boolean
  suffix?: string
  fallback?: string
}) {
  const inner = (
    <div className={
      'rounded-lg border bg-white p-4 transition-shadow ' +
      (link ? 'cursor-pointer hover:shadow-md ' : '') +
      (highlight
        ? 'border-amber-300 ring-1 ring-amber-200'
        : 'border-neutral-200')
    }>
      <div className="flex items-center justify-between">
        <span className={'inline-flex h-8 w-8 items-center justify-center rounded-md ' + accent}>
          {icon}
        </span>
        {link && <ArrowRight className="h-4 w-4 text-neutral-300" />}
      </div>
      <p className="mt-3 text-xs font-semibold uppercase tracking-wider text-neutral-500">
        {label}
      </p>
      <p className="mt-1 text-3xl font-bold text-neutral-900 tabular-nums">
        {loading
          ? '–'
          : value === undefined
            ? (fallback ?? '0')
            : `${value}${suffix ?? ''}`}
      </p>
    </div>
  )
  return link ? <Link to={link}>{inner}</Link> : inner
}
