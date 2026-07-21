import { useNavigate } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { TaskInbox } from '@/components/dashboard/TaskInbox'
import { PrPipeline } from '@/components/dashboard/PrPipeline'
import { Card } from '@/components/ui/card'
import { useDashboard } from '@/hooks/useDashboard'
import { useTasks } from '@/hooks/useTasks'
import { taskHref } from '@/lib/taskTypes'
import type { PrPipelineItem } from '@/components/dashboard/PrPipeline'
import type { DocumentStatus, TaskItem } from '@/types'
import type { ApiTask } from '@/services/tasks'
import type { PrPipelineItem as ApiPrPipelineItem } from '@/services/dashboard'

function mapPipeline(items: ApiPrPipelineItem[] | undefined): PrPipelineItem[] {
  if (!items) return []
  return items.map((pr) => ({
    id: pr.id,
    number: pr.number,
    title: pr.title,
    status: pr.status as DocumentStatus,
    amount: Number(pr.amount),
    pos: pr.pos.map((po) => ({
      id: po.id,
      number: po.number,
      status: po.status as DocumentStatus,
      vendor: po.vendor_name,
      grs: po.grs.map((gr) => ({
        id: gr.id,
        number: gr.number,
        type: gr.gr_type as 'physical' | 'service',
        status: gr.status as DocumentStatus,
        acknowledged: gr.status !== 'pending_ack',
        collectionPending: gr.status === 'collection_pending',
      })),
      invoices: po.invoices.map((inv) => ({
        id: inv.id,
        number: inv.internal_ref,
        status: inv.status as DocumentStatus,
        amount: Number(inv.total_amount),
      })),
      pa: po.pas[0]
        ? { id: po.pas[0].id, number: po.pas[0].pa_number, status: po.pas[0].status as DocumentStatus }
        : undefined,
    })),
  }))
}

function mapTasks(tasks: ApiTask[]): TaskItem[] {
  return tasks.map((t) => ({
    id: t.id,
    type: t.type,
    priority: t.priority,
    title: t.title,
    description: t.description ?? '',
    documentId: t.document_id,
    documentNumber: t.document_number,
    dueDate: t.due_date,
    amount: t.amount,
    vendor: t.vendor,
    // Shared resolver — Budget Plan tasks return an absolute Finance handoff URL
    // (the TaskCard detects it and jumps instead of using react-router); create_pa
    // and create_prepayment_pa route to the PA create page anchored on the PO.
    href: taskHref(t),
  }))
}

export default function RequesterDashboard() {
  const navigate = useNavigate()
  const { data } = useDashboard()
  const { data: tasksData } = useTasks({ is_completed: false })

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  const pipeline = mapPipeline(data?.pr_pipeline)
  const openTasks = mapTasks(tasksData?.items ?? [])

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-neutral-900">Dashboard</h1>

      {/* Stats bar */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard title="Active PRs" value={kpi('Active PRs')?.value ?? '—'} subtitle="In progress" />
        <StatCard title="Pending Payments" value={kpi('Pending Payments')?.value ?? '—'} subtitle="Payment applications" />
        <StatCard title="Paid This Month" value={kpi('Paid This Month')?.value ?? '—'} subtitle="Current month" />
        <StatCard
          title="Overdue Tasks"
          value={kpi('Overdue Tasks')?.value ?? '—'}
          subtitle="Requires attention"
          alert={kpi('Overdue Tasks')?.alert}
        />
      </div>

      {/* Main content */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Task Inbox */}
        <div className="lg:col-span-5">
          <Card className="p-5">
            <TaskInbox tasks={openTasks} maxItems={8} onViewAll={() => navigate('/tasks')} />
          </Card>
        </div>

        {/* PR Pipeline */}
        <div className="lg:col-span-7">
          <Card className="p-5">
            <PrPipeline items={pipeline} />
          </Card>
        </div>
      </div>
    </div>
  )
}
