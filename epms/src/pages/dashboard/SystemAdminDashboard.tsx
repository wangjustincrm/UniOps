import { Link } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { useDashboard } from '@/hooks/useDashboard'
import { ROLE_LABELS } from '@/stores/user.store'
import type { UserRole } from '@/types'

const QUICK_LINKS: { label: string; href: string; description: string }[] = [
  { label: 'User Management',       href: '/admin',    description: 'Manage users, roles and access' },
  { label: 'Approval Workflows',    href: '/admin',    description: 'Configure approval chains' },
  { label: 'Budget Configuration',  href: '/admin',    description: 'Set budget thresholds and accounts' },
  { label: 'Parts Catalog',         href: '/parts',    description: 'Manage materials and parts' },
  { label: 'Vendors',               href: '/vendors',  description: 'View and manage vendor registry' },
  { label: 'Projects',              href: '/projects', description: 'Manage project codes and budgets' },
]

export default function SystemAdminDashboard() {
  const { data } = useDashboard()

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  const roleRows = data?.users_by_role ?? []

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-neutral-900">System Admin Dashboard</h1>

      {/* Stats bar */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard title="Total Users" value={kpi('Total Users')?.value ?? '—'} subtitle="All registered users" />
        <StatCard title="Active Users" value={kpi('Active Users')?.value ?? '—'} subtitle="Currently active accounts" />
        <StatCard title="Departments" value={kpi('Departments')?.value ?? '—'} subtitle="Active departments" />
        <StatCard title="System Health" value={kpi('System Health')?.value ?? 'Healthy'} subtitle="All systems operational" />
      </div>

      {/* Main content */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Users by role */}
        <div className="lg:col-span-6">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-neutral-900">Users by Role</h2>
              <Link to="/admin" className="text-xs text-primary-600 hover:underline">Manage Users →</Link>
            </div>
            {roleRows.length === 0 ? (
              <p className="py-8 text-center text-sm text-neutral-400">No users found.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-neutral-100 text-left">
                      <th className="pb-2 font-medium text-neutral-500">Role</th>
                      <th className="pb-2 text-right font-medium text-neutral-500">Users</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-neutral-50">
                    {roleRows.map(({ role, count }) => (
                      <tr key={role} className="hover:bg-neutral-50">
                        <td className="py-2 pr-3 text-neutral-700">
                          {ROLE_LABELS[role as UserRole] ?? role}
                        </td>
                        <td className="py-2 text-right font-mono font-semibold text-neutral-900">{count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        {/* Quick links */}
        <div className="lg:col-span-6">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5">
            <h2 className="mb-4 text-base font-semibold text-neutral-900">Admin Sections</h2>
            <div className="flex flex-col gap-2">
              {QUICK_LINKS.map((item) => (
                <Link
                  key={item.label}
                  to={item.href}
                  className="rounded-lg border border-neutral-200 bg-white p-3 flex items-center justify-between hover:bg-primary-50 transition-colors"
                >
                  <div>
                    <p className="text-sm font-medium text-neutral-800">{item.label}</p>
                    <p className="text-xs text-neutral-500">{item.description}</p>
                  </div>
                  <span className="text-neutral-400 ml-2">→</span>
                </Link>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
