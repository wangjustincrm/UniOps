import { useSearchParams } from 'react-router-dom'
import { Loader2, Truck } from 'lucide-react'
import { useFreightAccess } from '@/hooks/useFreight'
import { ShipmentsTab } from './ShipmentsTab'
import { CarriersTab } from './CarriersTab'
import { Tabs } from '@uniops/shell'

type TabKey = 'shipments' | 'carriers'

const TAB_LABEL: Record<TabKey, string> = {
  shipments: 'Shipments',
  carriers: 'Carriers',
}

export default function FreightPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { data: access, isLoading, isError } = useFreightAccess()

  const canManage = !!access?.manage
  const canRegister = !!access?.register || canManage
  const canAdmin = !!access?.admin
  const tabs: TabKey[] = [
    ...(canRegister ? ['shipments'] as TabKey[] : []),
    ...(canAdmin ? ['carriers'] as TabKey[] : []),
  ]
  const requested = searchParams.get('tab') as TabKey | null
  const active: TabKey = requested && tabs.includes(requested) ? requested : (tabs[0] ?? 'shipments')

  const selectTab = (t: TabKey) => setSearchParams({ tab: t }, { replace: true })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="h-6 w-6 animate-spin text-fg-faint" />
      </div>
    )
  }

  if (isError || tabs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <Truck className="h-10 w-10 text-fg-faint mb-3" />
        <p className="text-sm font-medium text-fg-muted">You do not have access to Freight</p>
        <p className="text-xs text-fg-muted mt-1">Ask an administrator for the Freight register or manage permission.</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold text-fg">Freight</h1>
        <p className="text-sm text-fg-muted mt-0.5">
          Register courier shipments and maintain carriers — courier invoices are uploaded and matched on the Invoices page, and paid from the PA page
        </p>
      </div>

      {tabs.length > 1 && (
        <Tabs items={tabs.map((t) => ({ key: t, label: TAB_LABEL[t] }))} value={active} onChange={selectTab} />
      )}

      {active === 'shipments' && canRegister && <ShipmentsTab canManage={canManage} />}
      {active === 'carriers' && canAdmin && <CarriersTab />}
    </div>
  )
}
