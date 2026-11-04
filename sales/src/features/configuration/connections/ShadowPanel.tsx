import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionButton, ConfirmAction, ErrorMessage, Hint, Section, Skeleton } from '@/platform-ui'
import { saveShadowSetting } from './connectionsApi'
import { SHADOW_KEY, useShadowSetting } from './connectionSettings'

/** The shadow-run switch (design §5.4; sales.config, audited). On: files stay
 *  where Zoho reads them, orders received are Zoho's, no 940 is made or sent,
 *  only intake problems raise tasks. Either way it asks first: turning it on
 *  marks the open orders as Zoho's for good, and it says how many. */
export function ShadowPanel() {
  const qc = useQueryClient()
  const q = useShadowSetting()
  const [confirming, setConfirming] = useState(false)
  const [marked, setMarked] = useState<number | null>(null)
  const m = useMutation({
    mutationFn: (d: { shadow_mode: boolean }) => saveShadowSetting(d),
    onSuccess: async (r) => {
      setMarked(r.marked_orders ?? null)
      await qc.invalidateQueries({ queryKey: SHADOW_KEY })
    },
  })
  const on = q.data?.shadow_mode
  const open = q.data?.open_orders
  const orders = open == null ? 'Every open order' : `${open} open ${open === 1 ? 'order' : 'orders'}`
  return (
    <Section label="Shadow run" plain className="text-sm">
      {q.isLoading ? <Skeleton className="h-8 w-full" /> : q.error ? <ErrorMessage error={q.error} /> : (
        <>
          <p className={on ? 'text-warning-700' : 'text-neutral-600'}>
            {on ? 'On: Zoho is live. Files are read and left in place, orders open at the switch-on and received since are Zoho\'s, no 940 is made or sent, and only intake problems raise tasks.'
              : 'Off: Sales works the orders it receives and sends their 940s.'}
          </p>
          {on && marked != null && <Hint as="p">{marked} open order(s) were marked as Zoho's.</Hint>}
          <span>
            <ActionButton size="sm" variant="secondary" permission="sales.config" onAction={() => setConfirming(true)}>
              {on ? 'Turn the shadow run off' : 'Turn the shadow run on'}
            </ActionButton>
          </span>
          {on ? (
            <ConfirmAction open={confirming} onOpenChange={setConfirming} title="Turn the shadow run off?" destructive
              description="Sales then moves or deletes files after reading them as each connection says, and sends 940s for the orders it receives from now on. Orders marked during the shadow run stay Zoho's."
              confirmLabel="Turn off" onConfirm={() => m.mutateAsync({ shadow_mode: false })} />
          ) : (
            <ConfirmAction open={confirming} onOpenChange={setConfirming} title="Turn the shadow run on?" destructive
              description={`Zoho becomes the live system: files are read and left in place, and no 940 is made or sent. ${orders} will be marked as Zoho's, and stay Zoho's when the shadow run ends.`}
              confirmLabel="Turn on" onConfirm={() => m.mutateAsync({ shadow_mode: true })} />
          )}
        </>
      )}
    </Section>
  )
}
