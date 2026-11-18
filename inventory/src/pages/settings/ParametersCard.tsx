// Inventory parameters (inv_params). Each key is its own PUT
// (/settings/params/{key}); the server validates and answers 422 with a
// readable detail, which is shown as-is.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { Button, Card, CardContent, CardHeader, CardTitle, Input, Label, Select } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import type { useToasts } from '@/hooks/useToasts'
import {
  settingsApi, PARAM_AMOUNT_TOLERANCE, PARAM_NC_TIME_ZONE, PARAM_QTY_TOLERANCE, PARAM_SYNC_INTERVAL,
  type ParamValues,
} from './settingsApi'

const TIME_ZONE_LABELS: Record<string, string> = {
  'Asia/Shanghai': "Asia/Shanghai — China — NC's hard-coded clock (default)",
  'America/Toronto': 'America/Toronto',
}

interface Draft {
  interval: string
  timeZone: string
  qtyTol: string
  amountTol: string
}

function toDraft(v: ParamValues): Draft {
  return {
    interval: String(v[PARAM_SYNC_INTERVAL]),
    timeZone: v[PARAM_NC_TIME_ZONE],
    qtyTol: String(v[PARAM_QTY_TOLERANCE]),
    amountTol: String(v[PARAM_AMOUNT_TOLERANCE]),
  }
}

/** Text → JSON number for the API. Anything that is not a finite number is
 *  sent as the raw string so the server's 422 explains what is wrong, rather
 *  than this form silently sending something else. */
function asNumber(s: string): number | string {
  const t = s.trim()
  const n = Number(t)
  return t !== '' && Number.isFinite(n) ? n : t
}


export function ParametersCard({ canAdmin, toasts }: {
  canAdmin: boolean
  toasts: ReturnType<typeof useToasts>
}) {
  const queryClient = useQueryClient()
  const q = useQuery({ queryKey: ['inv-params'], queryFn: settingsApi.params })
  // null = untouched: the form shows the server values. Edits create a draft;
  // a successful save drops it again so the refetched values show.
  const [edited, setEdited] = useState<Draft | null>(null)
  const [error, setError] = useState<string | null>(null)
  const draft = edited ?? (q.data ? toDraft(q.data.values) : null)
  const setDraft = (d: Draft) => setEdited(d)

  const save = useMutation({
    mutationFn: async (d: Draft) => {
      const current = toDraft(q.data!.values)
      const changes: [string, unknown][] = []
      if (d.interval !== current.interval) changes.push([PARAM_SYNC_INTERVAL, asNumber(d.interval)])
      if (d.timeZone !== current.timeZone) changes.push([PARAM_NC_TIME_ZONE, d.timeZone])
      if (d.qtyTol !== current.qtyTol) changes.push([PARAM_QTY_TOLERANCE, asNumber(d.qtyTol)])
      if (d.amountTol !== current.amountTol) changes.push([PARAM_AMOUNT_TOLERANCE, asNumber(d.amountTol)])
      // One at a time, stopping at the first refusal: what was saved before it
      // stays saved and the refetch below shows exactly that.
      for (const [key, value] of changes) await settingsApi.putParam(key, value)
      return changes.length
    },
    onMutate: () => setError(null),
    onSuccess: (n) => {
      setEdited(null)
      toasts.success(n === 0 ? 'Nothing to save.' : 'Parameters saved.')
    },
    onError: (err) => {
      setError(err instanceof ApiError || err instanceof Error ? err.message : 'Save failed.')
    },
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ['inv-params'] })
      // The interval shows on the sync status card too.
      await queryClient.invalidateQueries({ queryKey: ['inv-nc-sync-status'] })
    },
  })

  const zones = q.data?.nc_time_zones ?? Object.keys(TIME_ZONE_LABELS)
  const defaults = q.data?.defaults
  const dirty = !!(draft && q.data && JSON.stringify(draft) !== JSON.stringify(toDraft(q.data.values)))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Parameters</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {q.isLoading && <p className="text-sm text-fg-muted">Loading…</p>}
        {q.isError && <p className="text-sm text-danger-fg">Could not load parameters: {(q.error as Error).message}</p>}
        {draft && (
          <form
            className="space-y-4"
            onSubmit={(e) => { e.preventDefault(); if (!save.isPending) save.mutate(draft) }}
          >
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="p-interval">NC sync interval (minutes)</Label>
                <Input
                  id="p-interval" inputMode="numeric" value={draft.interval} disabled={!canAdmin}
                  onChange={(e) => setDraft({ ...draft, interval: e.target.value })}
                />
                <p className="text-xs text-fg-muted">
                  0 = automatic sync off.{defaults && ` Default ${defaults[PARAM_SYNC_INTERVAL]}.`}
                </p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="p-tz">NC time zone</Label>
                <Select
                  id="p-tz" className="w-full" value={draft.timeZone} disabled={!canAdmin}
                  onChange={(e) => setDraft({ ...draft, timeZone: e.target.value })}
                >
                  {zones.map((z) => <option key={z} value={z}>{TIME_ZONE_LABELS[z] ?? z}</option>)}
                </Select>
                <p className="text-xs text-fg-muted">The clock NC writes business times in.</p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="p-qty">Quantity tolerance</Label>
                <Input
                  id="p-qty" inputMode="decimal" value={draft.qtyTol} disabled={!canAdmin}
                  onChange={(e) => setDraft({ ...draft, qtyTol: e.target.value })}
                />
                <p className="text-xs text-fg-muted">
                  Differences at or below this are treated as equal.{defaults && ` Default ${defaults[PARAM_QTY_TOLERANCE]}.`}
                </p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="p-amt">Amount tolerance</Label>
                <Input
                  id="p-amt" inputMode="decimal" value={draft.amountTol} disabled={!canAdmin}
                  onChange={(e) => setDraft({ ...draft, amountTol: e.target.value })}
                />
                <p className="text-xs text-fg-muted">
                  Same, for amounts.{defaults && ` Default ${defaults[PARAM_AMOUNT_TOLERANCE]}.`}
                </p>
              </div>
            </div>
            {error && (
              <p role="alert" className="rounded-lg border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
                {error}
              </p>
            )}
            {canAdmin && (
              <div className="flex justify-end gap-2">
                <Button
                  type="button" variant="secondary" size="sm"
                  disabled={!dirty || save.isPending}
                  onClick={() => { setError(null); setEdited(null) }}
                >
                  Discard
                </Button>
                <Button type="submit" size="sm" disabled={!dirty || save.isPending}>
                  {save.isPending && <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />}
                  Save
                </Button>
              </div>
            )}
          </form>
        )}
      </CardContent>
    </Card>
  )
}
