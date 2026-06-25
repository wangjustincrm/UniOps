import { create } from 'zustand'
import type { TaskItem } from '@/types'

// ─── Types ────────────────────────────────────────────────────────────────────

export interface TaskRecord extends TaskItem {
  createdAt: string
  completedAt?: string
  completedBy?: string
}

// ─── Demo tasks (migrated from RequesterDashboard hardcoded DEMO_TASKS) ───────

const DEMO_TASKS: TaskRecord[] = [
  {
    id: 't1',
    type: 'collect_goods',
    priority: 'urgent',
    title: 'Collect Goods',
    description: '',
    documentId: 'po1',
    documentNumber: 'PO-ABC-2603-01',
    dueDate: '2 days',
    amount: 3200,
    vendor: 'ABC Supplies',
    href: '/gr/gr1/collect',
    createdAt: '2026-03-20T08:00:00.000Z',
  },
  {
    id: 't2',
    type: 'acknowledge_gr',
    priority: 'urgent',
    title: 'Acknowledge GR',
    description: '',
    documentId: 'gr2',
    documentNumber: 'GR-20260401-0002',
    dueDate: '1 day',
    amount: 8500,
    vendor: 'DEF Corp',
    href: '/gr/gr2',
    createdAt: '2026-03-21T08:00:00.000Z',
  },
  {
    id: 't3',
    type: 'revise_pa',
    priority: 'normal',
    title: 'Return for Revision',
    description: '',
    documentId: 'pa1',
    documentNumber: 'PA-20260317-0001',
    href: '/pa/pa1',
    createdAt: '2026-03-19T10:00:00.000Z',
  },
  {
    id: 't4',
    type: 'settle_prepayment',
    priority: 'normal',
    title: 'Settle Prepayment',
    description: '',
    documentId: 'pa2',
    documentNumber: 'PA-20260330-0002',
    amount: 32770,
    vendor: 'Salesforce Canada',
    href: '/pa/pa2/settle',
    createdAt: '2026-03-31T10:00:00.000Z',
  },
  {
    id: 't5',
    type: 'revise_pr',
    priority: 'normal',
    title: 'Revise PR',
    description: '',
    documentId: 'pr3',
    documentNumber: 'PR-20260312-0005',
    href: '/pr/pr3',
    createdAt: '2026-03-18T09:00:00.000Z',
  },
]

// ─── Store ────────────────────────────────────────────────────────────────────

interface TaskState {
  tasks: TaskRecord[]
  addTask: (task: Omit<TaskRecord, 'id' | 'createdAt'>) => void
  completeTask: (id: string, completedBy?: string) => void
  getOpenTasks: () => TaskRecord[]
}

export const useTaskStore = create<TaskState>()((set, get) => ({
  tasks: DEMO_TASKS,

  addTask: (task) => {
    // Deduplicate: skip if open task with same type + documentId already exists
    const existing = get().tasks.find(
      (t) => t.type === task.type && t.documentId === task.documentId && !t.completedAt
    )
    if (existing) return

    const record: TaskRecord = {
      ...task,
      id: crypto.randomUUID(),
      createdAt: new Date().toISOString(),
    }
    set((s) => ({ tasks: [record, ...s.tasks] }))
  },

  completeTask: (id, completedBy) =>
    set((s) => ({
      tasks: s.tasks.map((t) =>
        t.id === id
          ? { ...t, completedAt: new Date().toISOString(), completedBy }
          : t
      ),
    })),

  getOpenTasks: () => get().tasks.filter((t) => !t.completedAt),
}))
