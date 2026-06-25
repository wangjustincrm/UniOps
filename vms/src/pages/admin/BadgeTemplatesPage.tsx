/** Structured badge editor — every label, field, and style is editable here.
 *
 * Edits a single global BadgeConfig (vms_config.badge_config). The live
 * preview uses the exact same BadgePreview renderer the print page uses, so
 * what you see is what prints. No HTML/CSS — pure form controls.
 */
import { useMemo, useState } from 'react'
import { CheckCircle2, Loader2, ArrowUp, ArrowDown } from 'lucide-react'
import {
  useBadgeConfig, useUpdateBadgeConfig, BADGE_CONFIG_DEFAULTS,
  type BadgeConfig, type AccessArea,
  type Visit, type Visitor, type UserBrief,
} from '@/services/api'
import { BadgePreview } from '@/components/BadgePreview'

const AREA_ORDER: AccessArea[] = [
  'office', 'warehouse', 'production_non_gmp', 'production_gmp', 'laboratory', 'all',
]

// Sample data so the preview shows a realistic badge while editing.
const SAMPLE_VISIT = {
  id: '00000000-0000-0000-0000-000000000000',
  visit_date: new Date().toISOString().slice(0, 10),
  planned_departure: new Date(Date.now() + 2 * 3600_000).toISOString(),
  visit_purpose: 'meeting',
  access_area: 'production_gmp',
  accompanying_count: 2,
  vehicle_plate: 'ABC-123',
} as unknown as Visit
const SAMPLE_VISITOR = {
  first_name: 'Jordan', last_name: 'Lee', company_name: 'Acme Foods Ltd.',
} as unknown as Visitor
const SAMPLE_HOST = { id: 'h', full_name: 'Pat Morgan' } as unknown as UserBrief

const inputCls =
  'rounded-md border border-neutral-300 px-2 py-1 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500'
const sectionCls = 'rounded-md border border-neutral-200 bg-white p-3 space-y-2'
const headCls = 'text-xs font-semibold uppercase tracking-wider text-neutral-500'

export default function BadgeTemplatesPage() {
  const { data, isLoading } = useBadgeConfig()
  const save = useUpdateBadgeConfig()

  const server = useMemo<BadgeConfig>(() => data ?? BADGE_CONFIG_DEFAULTS, [data])
  const [draft, setDraft] = useState<BadgeConfig | null>(null)
  const cfg = draft ?? server
  const dirty = draft !== null

  const update = (mut: (c: BadgeConfig) => void) => {
    const next: BadgeConfig = structuredClone(cfg)
    mut(next)
    setDraft(next)
  }

  const onSave = () => {
    save.mutate(cfg, { onSuccess: () => setDraft(null) })
  }

  const moveMeta = (i: number, dir: -1 | 1) => update((c) => {
    const j = i + dir
    if (j < 0 || j >= c.meta_fields.length) return
    ;[c.meta_fields[i], c.meta_fields[j]] = [c.meta_fields[j], c.meta_fields[i]]
  })

  return (
    <div className="max-w-5xl">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold text-neutral-900">Badge</h2>
          <p className="mt-0.5 text-xs text-neutral-500">
            Edit every label, field, and style on the printed visitor badge.
            The preview matches what prints.
          </p>
        </div>
        <button
          onClick={onSave}
          disabled={!dirty || save.isPending}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
        >
          {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
          Save
        </button>
      </div>

      {save.error && (
        <p className="mt-2 rounded-md bg-danger-50 px-3 py-2 text-xs text-danger-600">{save.error.message}</p>
      )}
      {isLoading && <p className="mt-3 text-xs text-neutral-400">Loading…</p>}

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* ── Form ── */}
        <div className="space-y-3">
          {/* Band */}
          <div className={sectionCls}>
            <p className={headCls}>Top band</p>
            <label className="block text-xs text-neutral-600">Title
              <input className={`${inputCls} mt-1 w-full`} value={cfg.band.title}
                onChange={(e) => update((c) => { c.band.title = e.target.value })} />
            </label>
            <div className="flex flex-wrap gap-4 text-xs text-neutral-600">
              <label className="inline-flex items-center gap-1.5">
                <input type="checkbox" checked={cfg.band.show_zone}
                  onChange={(e) => update((c) => { c.band.show_zone = e.target.checked })} /> Show zone
              </label>
              <label className="inline-flex items-center gap-1.5">
                <input type="checkbox" checked={cfg.band.show_risk}
                  onChange={(e) => update((c) => { c.band.show_risk = e.target.checked })} /> Show risk
              </label>
              <label className="inline-flex items-center gap-1.5">Risk suffix
                <input className={`${inputCls} w-20`} value={cfg.band.risk_suffix}
                  onChange={(e) => update((c) => { c.band.risk_suffix = e.target.value })} />
              </label>
            </div>
            <p className={headCls}>Zones (label + colors + risk)</p>
            <div className="space-y-1">
              {AREA_ORDER.map((a) => (
                <div key={a} className="flex items-center gap-2">
                  <span className="w-32 shrink-0 text-[11px] text-neutral-500">{a}</span>
                  <input className={`${inputCls} flex-1`} value={cfg.band.areas[a].label}
                    onChange={(e) => update((c) => { c.band.areas[a].label = e.target.value })} />
                  <input type="color" className="h-7 w-9 rounded border" value={cfg.band.areas[a].bg}
                    onChange={(e) => update((c) => { c.band.areas[a].bg = e.target.value.toUpperCase() })} title="background" />
                  <input type="color" className="h-7 w-9 rounded border" value={cfg.band.areas[a].fg}
                    onChange={(e) => update((c) => { c.band.areas[a].fg = e.target.value.toUpperCase() })} title="text" />
                  <input className={`${inputCls} w-24`} value={cfg.band.areas[a].risk}
                    onChange={(e) => update((c) => { c.band.areas[a].risk = e.target.value })} title="risk label" />
                </div>
              ))}
            </div>
          </div>

          {/* Identity */}
          <div className={sectionCls}>
            <p className={headCls}>Name & company</p>
            <div className="flex gap-4 text-xs text-neutral-600">
              <label className="inline-flex items-center gap-1.5">
                <input type="checkbox" checked={cfg.identity.name_uppercase}
                  onChange={(e) => update((c) => { c.identity.name_uppercase = e.target.checked })} /> Uppercase name
              </label>
              <label className="inline-flex items-center gap-1.5">
                <input type="checkbox" checked={cfg.identity.show_company}
                  onChange={(e) => update((c) => { c.identity.show_company = e.target.checked })} /> Show company
              </label>
            </div>
          </div>

          {/* Meta fields */}
          <div className={sectionCls}>
            <p className={headCls}>Detail rows</p>
            {cfg.meta_fields.map((f, i) => (
              <div key={f.key} className="flex items-center gap-2">
                <input type="checkbox" checked={f.visible}
                  onChange={(e) => update((c) => { c.meta_fields[i].visible = e.target.checked })} />
                <span className="w-32 shrink-0 text-[11px] text-neutral-500">{f.key}</span>
                <input className={`${inputCls} flex-1`} value={f.label}
                  onChange={(e) => update((c) => { c.meta_fields[i].label = e.target.value })} />
                <button onClick={() => moveMeta(i, -1)} disabled={i === 0}
                  className="rounded p-1 text-neutral-500 hover:bg-neutral-100 disabled:opacity-30"><ArrowUp className="h-3.5 w-3.5" /></button>
                <button onClick={() => moveMeta(i, 1)} disabled={i === cfg.meta_fields.length - 1}
                  className="rounded p-1 text-neutral-500 hover:bg-neutral-100 disabled:opacity-30"><ArrowDown className="h-3.5 w-3.5" /></button>
              </div>
            ))}
          </div>

          {/* QR */}
          <div className={sectionCls}>
            <p className={headCls}>QR code</p>
            <label className="inline-flex items-center gap-1.5 text-xs text-neutral-600">
              <input type="checkbox" checked={cfg.qr.show}
                onChange={(e) => update((c) => { c.qr.show = e.target.checked })} /> Show QR
            </label>
            {!cfg.qr.show && (
              <p className="rounded bg-amber-50 px-2 py-1 text-[11px] text-amber-700">
                Hiding the QR disables scan-to-check-out on the printed badge.
              </p>
            )}
            <label className="block text-xs text-neutral-600">QR caption
              <input className={`${inputCls} mt-1 w-full`} value={cfg.qr.label}
                onChange={(e) => update((c) => { c.qr.label = e.target.value })} />
            </label>
          </div>

          {/* Footer */}
          <div className={sectionCls}>
            <div className="flex items-center justify-between">
              <p className={headCls}>Footer lines</p>
              <button className="text-xs text-primary-700 hover:underline"
                onClick={() => update((c) => { c.footer.lines.push('') })}>+ Add line</button>
            </div>
            <label className="inline-flex items-center gap-1.5 text-xs text-neutral-600">
              <input type="checkbox" checked={cfg.footer.show}
                onChange={(e) => update((c) => { c.footer.show = e.target.checked })} /> Show footer
            </label>
            {cfg.footer.lines.map((line, i) => (
              <div key={i} className="flex items-center gap-2">
                <input className={`${inputCls} flex-1`} value={line}
                  onChange={(e) => update((c) => { c.footer.lines[i] = e.target.value })} />
                <button className="text-xs text-danger-600 hover:underline"
                  onClick={() => update((c) => { c.footer.lines.splice(i, 1) })}>Remove</button>
              </div>
            ))}
          </div>

          {/* Style */}
          <div className={sectionCls}>
            <p className={headCls}>Style</p>
            <div className="grid grid-cols-2 gap-2 text-xs text-neutral-600">
              <label>Name size (pt)
                <input type="number" className={`${inputCls} mt-1 w-full`} value={cfg.style.name_size_pt}
                  onChange={(e) => update((c) => { c.style.name_size_pt = Number(e.target.value) })} />
              </label>
              <label>Band title size (pt)
                <input type="number" className={`${inputCls} mt-1 w-full`} value={cfg.style.band_title_size_pt}
                  onChange={(e) => update((c) => { c.style.band_title_size_pt = Number(e.target.value) })} />
              </label>
              <label className="flex items-center gap-2">Text color
                <input type="color" className="h-7 w-9 rounded border" value={cfg.style.text_color}
                  onChange={(e) => update((c) => { c.style.text_color = e.target.value.toUpperCase() })} />
              </label>
              <label className="flex items-center gap-2">Company color
                <input type="color" className="h-7 w-9 rounded border" value={cfg.style.company_color}
                  onChange={(e) => update((c) => { c.style.company_color = e.target.value.toUpperCase() })} />
              </label>
              <label className="flex items-center gap-2">Footer color
                <input type="color" className="h-7 w-9 rounded border" value={cfg.style.footer_color}
                  onChange={(e) => update((c) => { c.style.footer_color = e.target.value.toUpperCase() })} />
              </label>
              <label>Name align
                <select className={`${inputCls} mt-1 w-full`} value={cfg.style.name_align}
                  onChange={(e) => update((c) => { c.style.name_align = e.target.value as BadgeConfig['style']['name_align'] })}>
                  <option value="left">left</option>
                  <option value="center">center</option>
                  <option value="right">right</option>
                </select>
              </label>
            </div>
          </div>
        </div>

        {/* ── Live preview ── */}
        <div className="lg:sticky lg:top-4 self-start">
          <p className={`${headCls} mb-1`}>Preview</p>
          <div className="overflow-auto rounded-md border border-neutral-200 bg-neutral-50 p-3">
            <BadgePreview visit={SAMPLE_VISIT} visitor={SAMPLE_VISITOR} host={SAMPLE_HOST} config={cfg} />
          </div>
        </div>
      </div>
    </div>
  )
}
