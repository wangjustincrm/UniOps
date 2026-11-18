// Loss rates — Phase 1C's planning parameters.
//
// BOMs are treated as EXACT data: loss is not written into bom_lines, it is
// applied when the requirement is computed, by inflating each component by
// the rate for its own category (packaging vs everything else).
//
// **The packaging rate starts at 0 and that is deliberate**, which is why
// this screen says so out loud rather than presenting an empty-looking
// field somebody would helpfully fill in. Powder and dry-mix BOMs carry no
// loss, but some packaging BOMs already have it baked into the quantity
// (S0093, 700g x 6: 600 cans theoretical, the BOM lists 610). A rate on top
// of that buys packaging nobody needs.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { Button, Input } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { capacityApi } from './capacityApi'

const RAW_KEY = 'raw_material_loss_rate'
const PACKAGING_KEY = 'packaging_loss_rate'

function asPercent(value: unknown): string {
  // Stored as a fraction (0.02 = 2%) because that is what the requirement
  // maths multiplies by; shown as a percent because that is how a plant
  // talks about loss. `undefined` (not loaded, or never set) is NOT 0 — a
  // field that shows 0 before the answer arrives states something the
  // system does not know yet.
  if (typeof value !== 'number') return ''
  return String(Math.round(value * 10000) / 100)
}

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

export function LossRatesSection({ canWrite }: { canWrite: boolean }) {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})

  const paramsQuery = useQuery({
    queryKey: ['mrp-params'],
    queryFn: () => capacityApi.getParams(),
  })

  const current: Record<string, string> = {
    [RAW_KEY]: asPercent(paramsQuery.data?.[RAW_KEY]),
    [PACKAGING_KEY]: asPercent(paramsQuery.data?.[PACKAGING_KEY]),
  }

  const mutation = useMutation({
    mutationFn: async ({ key, percent }: { key: string; percent: number }) =>
      capacityApi.setParam(key, percent / 100),
    onSuccess: async (_, { key, percent }) => {
      setError(null)
      setNote(`Saved — ${key === RAW_KEY ? 'raw material' : 'packaging'} loss rate is now ${percent}%.`)
      // DELETE the draft key rather than blanking it. `draft[key] ?? current[key]`
      // only falls back on null/undefined, so an empty string won a saved
      // value and left the box blank forever — the setting looked unset and
      // there was no way to see what it actually was.
      setDraft((d) => {
        const next = { ...d }
        delete next[key]
        return next
      })
      await queryClient.invalidateQueries({ queryKey: ['mrp-params'] })
    },
    onError: (err) => {
      setNote(null)
      setError(errMsg(err, 'Could not save the loss rate — please retry.'))
    },
  })

  function save(key: string) {
    const raw = draft[key]
    if (raw === undefined || raw.trim() === '') return
    const percent = Number(raw)
    if (!Number.isFinite(percent) || percent < 0 || percent > 100) {
      setError('Enter a percentage between 0 and 100.')
      return
    }
    setNote(null)
    mutation.mutate({ key, percent })
  }

  function field(key: string, label: string, hint: string) {
    const value = draft[key] ?? current[key]
    const dirty = draft[key] !== undefined && draft[key].trim() !== ''
      && draft[key].trim() !== current[key]
    return (
      <div key={key} className="flex flex-col gap-1">
        <label htmlFor={key} className="text-sm font-medium text-fg">{label}</label>
        <div className="flex items-center gap-2">
          <Input
            id={key}
            inputMode="decimal"
            value={value}
            onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
            disabled={!canWrite || paramsQuery.isLoading || mutation.isPending}
            className="w-24"
          />
          <span className="text-sm text-fg-muted">%</span>
          {canWrite && (
            <Button
              type="button" size="sm" className="min-h-[44px]"
              disabled={!dirty || mutation.isPending}
              onClick={() => save(key)}
            >
              {mutation.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Save
            </Button>
          )}
        </div>
        <p className="text-xs text-fg-muted">
          {/* The saved value, stated separately from the input. While
              somebody is typing, the box shows their draft — and without
              this line there is nothing on screen saying what the setting
              actually IS. */}
          <span className="font-medium text-fg-secondary">
            {paramsQuery.isLoading
              ? 'Loading current value…'
              : current[key] === ''
                ? 'Not set — treated as 0%'
                : `Currently ${current[key]}%`}
          </span>
          {' · '}{hint}
        </p>
      </div>
    )
  }

  return (
    <section className="rounded-lg border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-fg">Loss Rates</h2>
      <p className="mt-1 text-xs text-fg-muted">
        BOM quantities are treated as exact. Loss is added when material requirements are
        calculated: each component is inflated by the rate for its own category, at every
        level of the explosion.
      </p>

      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        {field(RAW_KEY, 'Raw material loss',
          'Applies to raw and auxiliary materials and semi-finished powder.')}
        {field(PACKAGING_KEY, 'Packaging loss',
          'Applies to packaging materials (codes starting CP).')}
      </div>

      <p className="mt-3 flex items-start gap-1.5 rounded-md border border-warning-line bg-warning-soft px-3 py-2 text-xs text-warning-fg">
        <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          <strong>Leave packaging loss at 0 for now.</strong> Powder and dry-mix BOMs carry
          no loss, but some packaging BOMs already include it in the quantity — S0093
          (700g × 6) lists 610 cans where 600 is the theoretical figure. Setting a rate here
          would inflate an already inflated number. Change it only once the packaging BOMs
          in NC are exact.
        </span>
      </p>

      {note && <p role="status" className="mt-2 text-xs text-success-fg">{note}</p>}
      {error && (
        <p role="alert" className="mt-2 flex items-center gap-1.5 rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
          <AlertTriangle className="h-4 w-4 shrink-0" /> {error}
        </p>
      )}
    </section>
  )
}
