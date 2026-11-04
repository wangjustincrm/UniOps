import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, ErrorMessage, FormSheet, StatCard } from '@/platform-ui'
import { EdiSettingsForm } from './EdiSettingsForm'
import { ShadowPanel } from './ShadowPanel'
import { useEdiSettings, useSchedulerPaused, useShadowSetting } from './connectionSettings'
import { useSchedulerEnabled } from './schedulerEnabled'

const WAIT = '…'

/** The module's connection settings as one row of small cards above the list;
 *  each opens its setting in a sheet (a reader sees it; sales.config changes it). */
export function ConnectionSummary() {
  const navigate = useNavigate()
  const [open, setOpen] = useState<'shadow' | 'runs' | 'edi' | null>(null)
  const shadow = useShadowSetting()
  const edi = useEdiSettings()
  const enabled = useSchedulerEnabled()
  const paused = useSchedulerPaused().data?.paused
  const s = edi.settings
  const runs = !enabled ? 'Off' : paused ? 'Paused' : 'On'
  const close = (o: boolean) => { if (!o) setOpen(null) }
  return (
    <section aria-label="Connection settings" className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard compact label="Shadow run" count={shadow.data ? (shadow.data.shadow_mode ? 'On' : 'Off') : WAIT}
          tone={shadow.data?.shadow_mode ? 'warning' : 'neutral'} onOpen={() => setOpen('shadow')} />
        <StatCard compact label="Automatic runs" count={runs} tone={runs === 'On' ? 'neutral' : 'warning'} onOpen={() => setOpen('runs')} />
        <StatCard compact label="Warehouse" count={s ? edi.name(s.connection_id) : WAIT} onOpen={() => setOpen('edi')} />
        <StatCard compact label="997 wait" count={s ? `${s.ack_timeout_minutes} min` : WAIT} onOpen={() => setOpen('edi')} />
        <StatCard compact label="810 invoices" count={s ? edi.name(s.invoice_connection_id) : WAIT} onOpen={() => setOpen('edi')} />
      </div>
      <ErrorMessage error={shadow.error ?? edi.error} />
      <FormSheet open={open === 'shadow'} onOpenChange={close} title="Shadow run"><ShadowPanel /></FormSheet>
      <FormSheet open={open === 'runs'} onOpenChange={close} title="Automatic runs">
        <div className="flex flex-col gap-3 text-sm">
          <p>{!enabled ? 'Automatic runs are off in this environment: a connection is read only when someone uses Run now.'
            : paused ? 'Every job is paused: a connection is read only when someone uses Run now.'
              : 'Each connection runs on its own schedule.'}</p>
          <span><Button size="sm" variant="secondary" onClick={() => navigate('/health')}>Open Health → Scheduler</Button></span>
        </div>
      </FormSheet>
      <FormSheet open={open === 'edi'} onOpenChange={close} title="Warehouse EDI and 810 invoices">
        {open === 'edi' && s && <EdiSettingsForm settings={s} options={edi.options} onDone={() => setOpen(null)} />}
      </FormSheet>
    </section>
  )
}
