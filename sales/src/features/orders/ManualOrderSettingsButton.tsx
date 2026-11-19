import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { Button, FormDialog, SchemaForm, useCan, useLookup, type FieldSpec } from '@/platform-ui'
import { MANUAL_SETTINGS_KEY, fetchManualOrderSettings, saveManualOrderSettings, type ManualOrderSettings } from './ordersApi'

const reasonList = z.array(z.string().trim().min(1).max(40, 'At most 40 characters each')).max(20, 'At most 20')
const schema = z.object({
  express_offered: z.boolean(),
  ltl_from_lb: z.number({ message: 'Required' }).int().min(1, 'At least 1 lb').max(100000),
  reasons: reasonList.min(1, 'At least one reason'),
})
const errorMessages: Record<string, string> = {
  'auth.forbidden': 'You do not have permission to change the manual order settings.',
}
const fields: FieldSpec[] = [
  { name: 'reasons', label: 'Reasons', type: 'tags',
    hint: 'Why a manual order is made: one is chosen before it is submitted. Orders → Manual orders by reason lists them.' },
  { name: 'express_offered', label: 'Offer Purolator Express', type: 'checkbox', hint: 'Off: a manual order ships by Purolator (Ground) or LTL only.' },
  { name: 'ltl_from_lb', label: 'Suggest LTL above (lb)', type: 'number', required: true,
    hint: 'After Preview EA: an order heavier than this suggests LTL, else Purolator. A suggestion only.' },
]

/** Orders → Manual order settings (review 2026-11-19: where people look for them, not among the EDI settings).
 *  A reader sees them; sales.config changes them (audited). */
export function ManualOrderSettingsButton() {
  const [open, setOpen] = useState(false)
  const qc = useQueryClient()
  const canEdit = useCan('sales.config')
  const settings = useLookup(open ? MANUAL_SETTINGS_KEY : null, fetchManualOrderSettings)
  return (
    <>
      <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>Manual order settings</Button>
      <FormDialog open={open} onOpenChange={setOpen} title="Manual order settings">
        {settings.data && (
          <SchemaForm schema={schema} fields={fields} errorMessages={errorMessages} readOnly={!canEdit} onCancel={() => setOpen(false)}
            defaultValues={{ ...settings.data }} done="Saved" after={() => setOpen(false)}
            onSubmit={async (v) => {
              const saved = await saveManualOrderSettings(v as unknown as ManualOrderSettings)
              qc.setQueryData(MANUAL_SETTINGS_KEY, saved)
              return saved
            }} />
        )}
      </FormDialog>
    </>
  )
}
