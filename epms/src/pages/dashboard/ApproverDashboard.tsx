import { useMutation, useQueryClient } from '@tanstack/react-query'
import { StatCard } from '@/components/dashboard/StatCard'
import { PendingApprovals } from '@/components/dashboard/PendingApprovals'
import { BudgetOverview } from '@/components/dashboard/BudgetOverview'
import { useDashboard } from '@/hooks/useDashboard'
import type { ApprovalItem } from '@/components/dashboard/PendingApprovals'
import type { DocumentStatus } from '@/types'
import { prService } from '@/services/pr'
import { poService } from '@/services/po'
import { paService } from '@/services/pa'

function mapApprovals(
  items: NonNullable<ReturnType<typeof useDashboard>['data']>['pending_approvals'],
): ApprovalItem[] {
  if (!items) return []
  return items.map((item) => ({
    id: item.id,
    docType: item.doc_type as 'PR' | 'PO' | 'PA',
    number: item.number,
    title: item.title,
    requester: item.requester_name || undefined,
    department: item.dept_name || undefined,
    amount: Number(item.amount),
    status: item.status as DocumentStatus,
    submittedDaysAgo: item.submitted_days_ago,
    href: item.href,
  }))
}

export default function ApproverDashboard() {
  const { data } = useDashboard()
  const queryClient = useQueryClient()

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ['prs'] })
    queryClient.invalidateQueries({ queryKey: ['pos'] })
    queryClient.invalidateQueries({ queryKey: ['tasks'] })
    queryClient.invalidateQueries({ queryKey: ['dashboard'] })
  }

  const prActionMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'approve' | 'return' | 'reject' }) =>
      prService.action(id, { action }),
    onSuccess: invalidateAll,
  })

  const poActionMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'approve' | 'return' | 'reject' }) =>
      poService.action(id, { action }),
    onSuccess: invalidateAll,
  })

  const paActionMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'approve' | 'return' | 'reject' }) =>
      paService.action(id, { action }),
    onSuccess: invalidateAll,
  })

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  const approvals = mapApprovals(data?.pending_approvals)

  const handleAction = (id: string, action: 'approve' | 'return' | 'reject') => {
    const item = approvals.find((a) => a.id === id)
    if (!item) return
    if (item.docType === 'PR') {
      prActionMutation.mutate({ id, action })
    } else if (item.docType === 'PO') {
      poActionMutation.mutate({ id, action })
    } else if (item.docType === 'PA') {
      paActionMutation.mutate({ id, action })
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-neutral-900">Dashboard</h1>

      {/* Stats bar */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          title="Pending Approvals"
          value={kpi('Pending Approvals')?.value ?? '—'}
          subtitle="Awaiting your action"
          alert={kpi('Pending Approvals')?.alert}
        />
        <StatCard title="Open POs" value={kpi('Open POs')?.value ?? '—'} subtitle="In progress" />
        <StatCard title="Total Committed (FY)" value={kpi('Total Committed (FY)')?.value ?? '—'} subtitle="FY YTD" />
        <StatCard
          title="Over-Budget Depts"
          value={kpi('Over-Budget Depts')?.value ?? '—'}
          subtitle="Require attention"
          alert={kpi('Over-Budget Depts')?.alert}
        />
      </div>

      {/* Main content */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Pending approvals */}
        <div className="lg:col-span-8">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5">
            <PendingApprovals
              items={approvals}
              onApprove={(id) => handleAction(id, 'approve')}
              onReturn={(id) => handleAction(id, 'return')}
              onReject={(id) => handleAction(id, 'reject')}
            />
          </div>
        </div>

        {/* Budget overview */}
        <div className="lg:col-span-4">
          <BudgetOverview groups={data?.budget_overview?.groups} />
        </div>
      </div>
    </div>
  )
}
