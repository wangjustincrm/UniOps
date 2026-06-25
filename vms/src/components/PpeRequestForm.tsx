/** PPE request section embedded in the New Visit form.
 *
 * The Host ticks "PPE needed"; the form expands with one size group per
 * visitor on the appointment (primary + companions). On submit the parent
 * serializes this into the VisitCreate payload's `ppe_requested.items[]`;
 * when the visit clears approval the Janitor receives an email listing
 * each visitor's gear.
 *
 * Keeps a synthetic dictionary keyed by visitor_id so the form keeps each
 * person's selections stable when the companion list changes upstream.
 */
import { useMemo } from 'react'
import type {
  ClothingSize, FootwearKind, PpeItem, PpeRequest, ShoeSize, Visitor,
} from '@/services/api'

const CLOTHING_SIZES: ClothingSize[] = ['XS', 'S', 'M', 'L', 'XL', 'XXL', 'other']
const SHOE_SIZES: ShoeSize[] = ['7', '8', '9', '10', '11', '12', '13', '14', 'other']

interface Props {
  /** Primary + companion visitors. The form renders one size group per row. */
  visitors: Visitor[]
  value: PpeRequest | null
  onChange: (next: PpeRequest | null) => void
}

function blankItem(visitorId: string): PpeItem {
  return {
    visitor_id: visitorId,
    clothing_size: 'M',
    footwear: 'shoe_covers',
    shoe_size: null,
  }
}

export function PpeRequestForm({ visitors, value, onChange }: Props) {
  const enabled = value !== null

  // Reconcile the items list against the current visitor set on every
  // render — visitors can be added / removed upstream after the box was
  // ticked. `useMemo` keeps the array stable when nothing actually changed
  // so the inputs don't re-key.
  const items = useMemo<PpeItem[]>(() => {
    if (!value) return []
    // Defensive: `value.items` should always be an array given how `toggle`
    // and `updateItem` construct it, but a stale draft loaded from a
    // pre-refactor session (or an HMR mid-render glitch) might be missing
    // the field — fall back to `[]` so we re-seed cleanly per visitor.
    const existing = value.items ?? []
    const byVisitor = new Map(existing.map((it) => [it.visitor_id, it]))
    return visitors.map((v) => byVisitor.get(v.id) ?? blankItem(v.id))
  }, [value, visitors])

  const toggle = (on: boolean) => {
    if (on) {
      onChange({ items: visitors.map((v) => blankItem(v.id)), notes: null })
    } else {
      onChange(null)
    }
  }

  const updateItem = (visitorId: string, patch: Partial<PpeItem>) => {
    if (!value) return
    onChange({
      ...value,
      items: items.map((it) =>
        it.visitor_id === visitorId ? { ...it, ...patch } : it,
      ),
    })
  }

  return (
    <div className="space-y-3">
      <label className="flex cursor-pointer items-start gap-2.5 text-sm text-neutral-800">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => toggle(e.target.checked)}
          className="mt-0.5"
        />
        <div className="leading-tight">
          <p className="font-medium">PPE needed</p>
          <p className="text-xs text-neutral-500">
            One size group per visitor — Janitor is emailed after approval with each person's gear.
          </p>
        </div>
      </label>

      {enabled && value && visitors.map((v) => {
        const item = items.find((it) => it.visitor_id === v.id)
        if (!item) return null
        return (
          <div
            key={v.id}
            className="rounded-md border border-neutral-200 bg-neutral-50/40 p-3"
          >
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">
              {v.first_name} {v.last_name}
              <span className="ml-2 font-normal normal-case text-neutral-400">
                {v.company_name}
              </span>
            </p>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Field label="Clothing size">
                <select
                  value={item.clothing_size}
                  onChange={(e) => updateItem(v.id, {
                    clothing_size: e.target.value as ClothingSize,
                    clothing_size_other: e.target.value === 'other'
                      ? item.clothing_size_other ?? ''
                      : null,
                  })}
                  className={inputCls}
                >
                  {CLOTHING_SIZES.map((s) => (
                    <option key={s} value={s}>{s === 'other' ? 'Other' : s}</option>
                  ))}
                </select>
              </Field>

              {item.clothing_size === 'other' && (
                <Field label="Clothing size — describe">
                  <input
                    type="text"
                    value={item.clothing_size_other ?? ''}
                    onChange={(e) => updateItem(v.id, { clothing_size_other: e.target.value })}
                    placeholder="e.g. EU 56 / 3XL coat"
                    className={inputCls}
                  />
                </Field>
              )}

              <Field label="Footwear">
                <select
                  value={item.footwear}
                  onChange={(e) => updateItem(v.id, {
                    footwear: e.target.value as FootwearKind,
                    shoe_size: e.target.value === 'shoes' ? item.shoe_size ?? '10' : null,
                    shoe_size_other: null,
                  })}
                  className={inputCls}
                >
                  <option value="shoe_covers">Shoe covers</option>
                  <option value="shoes">Safety shoes</option>
                </select>
              </Field>

              {item.footwear === 'shoes' && (
                <Field label="Shoe size (US)">
                  <select
                    value={item.shoe_size ?? ''}
                    onChange={(e) => updateItem(v.id, {
                      shoe_size: e.target.value as ShoeSize,
                      shoe_size_other: e.target.value === 'other'
                        ? item.shoe_size_other ?? ''
                        : null,
                    })}
                    className={inputCls}
                  >
                    {SHOE_SIZES.map((s) => (
                      <option key={s} value={s}>{s === 'other' ? 'Other' : s}</option>
                    ))}
                  </select>
                </Field>
              )}

              {item.footwear === 'shoes' && item.shoe_size === 'other' && (
                <Field label="Shoe size — describe">
                  <input
                    type="text"
                    value={item.shoe_size_other ?? ''}
                    onChange={(e) => updateItem(v.id, { shoe_size_other: e.target.value })}
                    placeholder="e.g. EU 47"
                    className={inputCls}
                  />
                </Field>
              )}
            </div>
          </div>
        )
      })}

      {enabled && value && (
        <Field label="Notes (optional — applies to the whole group)">
          <textarea
            value={value.notes ?? ''}
            onChange={(e) => onChange({ ...value, notes: e.target.value })}
            rows={2}
            placeholder="Anything else the Janitor should know…"
            className={inputCls}
          />
        </Field>
      )}
    </div>
  )
}

const inputCls =
  'block w-full rounded-md border border-neutral-300 bg-white px-2.5 py-1.5 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500'

function Field({
  label, children, className,
}: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={'block text-xs ' + (className ?? '')}>
      <span className="mb-0.5 block text-neutral-600">{label}</span>
      {children}
    </label>
  )
}
