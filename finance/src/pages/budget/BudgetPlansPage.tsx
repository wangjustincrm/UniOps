import EpmsEmbed from './EpmsEmbed'

export default function BudgetPlansPage() {
  return (
    <EpmsEmbed
      epmsPath="/budget/plans"
      title="Budget Plans"
      subtitle="Annual budget plans per Cost Center · Fiscal Year"
      activeKey="portal:/budget/plans"
    />
  )
}
