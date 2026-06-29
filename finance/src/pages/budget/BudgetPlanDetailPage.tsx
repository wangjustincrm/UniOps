import { useParams } from 'react-router-dom'
import EpmsEmbed from './EpmsEmbed'

/**
 * Deep-link target for a single Budget Plan, wrapped in Finance/Portal chrome.
 *
 * Budget Plan approval tasks (raised by the Approval Engine, surfaced in the
 * EPMS Task Inbox) hand off here so the plan opens inside the Finance module
 * rather than on EPMS's bare budget page. The plan editor itself still lives in
 * EPMS (`/budget/plans/:id`); we just embed it. The sidebar stays highlighted on
 * "Budget Plans" via the shared activeKey.
 */
export default function BudgetPlanDetailPage() {
  const { id } = useParams<{ id: string }>()
  return (
    <EpmsEmbed
      epmsPath={`/budget/plans/${id ?? ''}`}
      title="Budget Plan"
      subtitle="Review and approve the annual budget plan"
      activeKey="portal:/budget/plans"
    />
  )
}
