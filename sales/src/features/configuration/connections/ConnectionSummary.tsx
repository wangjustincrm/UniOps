import { useState } from 'react'
import { ErrorMessage, FormSheet, StatCard } from '@/platform-ui'
import { AutomaticRunsPanel } from './AutomaticRunsPanel'
import { EdiSettingsForm } from './EdiSettingsForm'
import { useAutomaticRuns, useEdiSettings } from './connectionSettings'

const WAIT = '…'
const CARD = 'w-36 shrink-0 sm:w-auto'
const RUNS = { on: 'On', off: 'Off', unavailable: 'Not available' } as const

/** The module's connection settings as one row of small cards above the list;
 *  each opens its setting in a sheet (a reader sees it; sales.config changes it). */
export function ConnectionSummary() {
  const [open, setOpen] = useState<'runs' | 'edi' | null>(null)
  const edi = useEdiSettings()
  const runs = useAutomaticRuns().state
  const s = edi.settings
  const close = (o: boolean) => { if (!o) setOpen(null) }
  return (
    <section aria-label="Connection settings" className="flex flex-col gap-2">
      {/* a phone: one row that scrolls sideways, so the table keeps the screen */}
      <div className="flex gap-2 overflow-x-auto pb-1 sm:grid sm:grid-cols-3 sm:overflow-visible sm:pb-0 lg:grid-cols-6">
        <StatCard compact className={CARD} label="Automatic runs" count={runs ? RUNS[runs] : WAIT} tone={runs === 'on' ? 'success' : 'neutral'} onOpen={() => setOpen('runs')} />
        <StatCard compact className={CARD} label="Warehouse" count={s ? edi.name(s.connection_id) : WAIT} onOpen={() => setOpen('edi')} />
        <StatCard compact className={CARD} label="997 wait" count={s ? `${s.ack_timeout_minutes} min` : WAIT} onOpen={() => setOpen('edi')} />
        <StatCard compact className={CARD} label="810 invoices" count={s ? edi.name(s.invoice_connection_id) : WAIT} onOpen={() => setOpen('edi')} />
        <StatCard compact className={CARD} label="Tasks go to" count={s ? edi.userName(s.operator_user_id) : WAIT}
          tone={s && !s.operator_user_id ? 'warning' : 'neutral'} onOpen={() => setOpen('edi')} />
      </div>
      <ErrorMessage error={edi.error} />
      <FormSheet open={open === 'runs'} onOpenChange={close} title="Automatic runs">{open === 'runs' && <AutomaticRunsPanel />}</FormSheet>
      <FormSheet open={open === 'edi'} onOpenChange={close} title="Warehouse EDI, 810 invoices and tasks">
        {open === 'edi' && s && <EdiSettingsForm settings={s} options={edi.options} onDone={() => setOpen(null)} />}
      </FormSheet>
    </section>
  )
}
