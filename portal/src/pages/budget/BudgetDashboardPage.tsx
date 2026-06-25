import EpmsEmbed from './EpmsEmbed'

export default function BudgetDashboardPage() {
  return (
    <EpmsEmbed
      epmsPath="/budget"
      title="Budget Dashboard"
      subtitle="Plan vs Actual · Annual budgets, commitments, and spend by Cost Center"
      activeKey="portal:/budget/dashboard"
    />
  )
}
