// The Trace page's two pickers: a material, then — scoped to it — a batch
// group. Options come from GET /trace/materials and /trace/batches
// (inventory-api/app/api/v1/trace.py); both list what has stock today first.
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge } from '@uniops/shell'
import { Combobox } from '@/components/Combobox'
import { ApiError } from '@/lib/api'
import { formatQty } from '@/lib/number'
import { useDebounced } from '@/pages/nc/useDebounced'
import { MatchBadge } from '@/pages/recon/ReconRows'
import { traceApi, type TraceBatchOption, type TraceMaterialOption } from './traceApi'
import { normCode } from './traceNav'

const DEBOUNCE_MS = 250

function errText(e: unknown, fallback: string): string {
  return e instanceof ApiError || e instanceof Error ? e.message || fallback : fallback
}

function Qtys({ nc, wms }: { nc: string | null; wms: string | null }) {
  return (
    <span className="shrink-0 whitespace-nowrap text-[11px] text-neutral-500">
      NC {formatQty(nc)} · WMS {formatQty(wms)}
    </span>
  )
}

// ── Material ───────────────────────────────────────────────────────────────

const materialKey = (q: string) => ['trace-materials', q] as const

/** Picks a material code. `value` is the chosen code (the parent remounts
 *  this with a key when the value changes from outside). */
export function MaterialPicker({ id, value, onChange, onDeselect, autoFocus, className }: {
  id?: string
  value: string
  onChange: (option: TraceMaterialOption) => void
  /** The text was edited away from the chosen code (or cleared). */
  onDeselect?: () => void
  autoFocus?: boolean
  className?: string
}) {
  const qc = useQueryClient()
  const [text, setText] = useState(value)
  const [open, setOpen] = useState(false)
  // The box still showing the chosen code means "not searching yet" — list
  // everything rather than just that one code.
  const search = useDebounced(text === value ? '' : text.trim(), DEBOUNCE_MS)

  const q = useQuery({
    queryKey: materialKey(search),
    queryFn: () => traceApi.materials(search),
    enabled: open,
    staleTime: 60_000,
  })
  const options = q.data?.items ?? []

  function choose(o: TraceMaterialOption) {
    setText(o.code)
    onChange(o)
  }

  function edit(next: string) {
    setText(next)
    if (value && normCode(next) !== value) onDeselect?.()
  }

  async function enterText(raw: string) {
    // A typed-in complete code: pick it if the server lists exactly that code
    // (asked directly — the debounced list may still be for a shorter prefix).
    const code = normCode(raw)
    if (!code) return
    const hit = options.find((o) => o.code === code)
      ?? (await qc.fetchQuery({ queryKey: materialKey(code), queryFn: () => traceApi.materials(code), staleTime: 60_000 }))
        .items.find((o) => o.code === code)
    if (hit) choose(hit)
  }

  return (
    <Combobox<TraceMaterialOption>
      id={id}
      className={className}
      inputClassName="font-mono uppercase"
      inputValue={text}
      onInputChange={edit}
      onOpenChange={setOpen}
      options={options}
      getKey={(o) => o.code}
      selectedKey={value || null}
      onSelect={choose}
      onEnterText={(t) => { void enterText(t) }}
      onClear={() => edit('')}
      loading={q.isFetching}
      errorText={q.isError ? errText(q.error, 'Could not load materials.') : null}
      emptyText={search ? `No material with movements matches “${search}”` : 'No materials'}
      placeholder="Code or name, e.g. CR0059"
      autoFocus={autoFocus}
      listMinWidth={440}
      renderOption={(o) => (
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-xs text-neutral-900">{o.code}</span>
              {!o.in_nc && <Badge variant="warning" className="text-[10px]">Not in NC</Badge>}
              {o.no_batch_since && (
                <Badge variant="neutral" className="text-[10px]">NC stopped by batch</Badge>
              )}
            </div>
            {o.name && <div className="truncate text-xs text-neutral-500">{o.name}</div>}
          </div>
          <Qtys nc={o.nc_qty} wms={o.wms_qty} />
        </div>
      )}
    />
  )
}

// ── Batch ──────────────────────────────────────────────────────────────────

/** The "(whole material)" entry: picking it clears the batch. */
const WHOLE = { whole: true } as const
type BatchItem = TraceBatchOption | typeof WHOLE
const isWhole = (o: BatchItem): o is typeof WHOLE => 'whole' in o
const batchKey = (o: BatchItem) => (isWhole(o) ? '' : `${o.batch}|${o.label}`)

/** Picks a batch group of `material`. `value` is the batch to trace by ('' =
 *  whole material) and `display` what the box shows for it (the group label
 *  when known). Remount with a key when material or value change outside. */
export function BatchPicker({ id, material, value, display, onChange, lockedNote, className }: {
  id?: string
  material: string | null
  value: string
  display?: string
  onChange: (batch: string, label: string) => void
  /** Set when batch tracing is not available for this material/date. */
  lockedNote?: string | null
  className?: string
}) {
  const shown = value ? (display || value) : ''
  const [text, setText] = useState(shown)
  const [open, setOpen] = useState(false)
  const search = useDebounced(text === shown ? '' : text.trim(), DEBOUNCE_MS)

  const disabled = !material || !!lockedNote
  const q = useQuery({
    queryKey: ['trace-batches', material, search],
    queryFn: () => traceApi.batches(material!, search),
    enabled: open && !disabled,
    staleTime: 60_000,
  })
  const items = q.data?.items ?? []
  const options: BatchItem[] = q.data ? [WHOLE, ...items] : []
  const total = q.data?.total ?? 0
  const selected = value ? items.find((o) => o.batch === value) : undefined

  function choose(o: BatchItem) {
    if (isWhole(o)) {
      setText('')
      onChange('', '')
    } else {
      setText(o.label)
      onChange(o.batch, o.label)
    }
  }

  return (
    <Combobox<BatchItem>
      id={id}
      className={className}
      inputClassName="font-mono"
      inputValue={text}
      onInputChange={setText}
      onOpenChange={setOpen}
      options={options}
      getKey={batchKey}
      selectedKey={value ? (selected ? batchKey(selected) : null) : ''}
      onSelect={choose}
      onEnterText={(t) => {
        // Enter on an empty box = whole material; otherwise pick the one
        // group whose label or batch matches what was typed.
        const typed = t.trim().toUpperCase()
        if (!typed) { if (value) choose(WHOLE); return }
        const hit = items.find((o) => o.label.toUpperCase() === typed || o.batch.toUpperCase() === typed)
        if (hit) choose(hit)
      }}
      onClear={() => { setText(''); if (value) onChange('', '') }}
      disabled={disabled}
      loading={q.isFetching}
      errorText={q.isError ? errText(q.error, 'Could not load batches.') : null}
      emptyText="No batches"
      placeholder={!material ? 'Select a material first' : lockedNote ? 'material level only' : 'whole material'}
      listMinWidth={540}
      footer={total > items.length
        ? `Showing ${items.length.toLocaleString('en-US')} of ${total.toLocaleString('en-US')} — type to narrow`
        : undefined}
      renderOption={(o) => isWhole(o) ? (
        <span className="text-xs italic text-neutral-600">(whole material)</span>
      ) : (
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-xs text-neutral-900">{o.label}</span>
              <MatchBadge match={o.match} />
            </div>
            <div className="text-[11px] text-neutral-500">
              supplier: {o.supplier_batches.length ? o.supplier_batches.join(', ') : '—'}
              {' · '}{o.wms_lots} WMS lot{o.wms_lots === 1 ? '' : 's'}
              {' · '}<span className="whitespace-nowrap">last moved {o.last_movement ?? '—'}</span>
            </div>
          </div>
          <Qtys nc={o.nc_qty} wms={o.wms_qty} />
        </div>
      )}
    />
  )
}
