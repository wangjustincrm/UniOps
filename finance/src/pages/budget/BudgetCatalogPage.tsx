import EpmsEmbed from './EpmsEmbed'

export default function BudgetCatalogPage() {
  return (
    <EpmsEmbed
      epmsPath="/budget/catalog"
      title="Account Catalog"
      subtitle="Shared L1 / L2 budget account templates · Factor configuration"
      activeKey="portal:/budget/catalog"
    />
  )
}
