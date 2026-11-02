import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ActionButton, ConfirmAction, ErrorMessage, Skeleton } from '@/platform-ui'
import { fetchShadowSetting, saveShadowSetting } from './connectionsApi'

const KEY = ['sales', 'config', 'shadow']

/** The shadow-run switch (design §5.4; sales.config, audited). On: files stay
 *  where Zoho reads them, orders received are Zoho's, no 940 is made or sent,
 *  only intake problems raise tasks. */
export function ShadowPanel() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: KEY, queryFn: fetchShadowSetting })
  const [confirming, setConfirming] = useState(false)
  const m = useMutation({ mutationFn: (d: { shadow_mode: boolean }) => saveShadowSetting(d), onSuccess: (r) => qc.setQueryData(KEY, r) })
  const on = q.data?.shadow_mode
  return (
    <section aria-label="Shadow run" className="flex flex-col gap-2 rounded-md border border-neutral-200 p-3 text-sm">
      <h2 className="font-semibold text-neutral-800">Shadow run</h2>
      {q.isLoading ? <Skeleton className="h-8 w-full" /> : q.error ? <ErrorMessage error={q.error} /> : (
        <>
          <p role="status" className={on ? 'text-warning-700' : 'text-neutral-600'}>
            {on ? 'On: Zoho is live. Files are read and left in place, orders received now are Zoho\'s, no 940 is made or sent, and only intake problems raise tasks.'
              : 'Off: Sales works the orders it receives and sends their 940s.'}
          </p>
          <ActionButton size="sm" variant="secondary" permission="sales.config"
            onAction={() => (on ? setConfirming(true) : m.mutateAsync({ shadow_mode: true }).catch(() => {}))}>
            {on ? 'Turn the shadow run off' : 'Turn the shadow run on'}
          </ActionButton>
          <ConfirmAction open={confirming} onOpenChange={setConfirming} title="Turn the shadow run off?" destructive
            description="Sales then moves or deletes files after reading them as each connection says, and sends 940s for the orders it receives from now on. Orders received during the shadow run stay Zoho's."
            confirmLabel="Turn off" onConfirm={() => m.mutateAsync({ shadow_mode: false })} />
          {!confirming && <ErrorMessage error={m.error} />}
        </>
      )}
    </section>
  )
}
