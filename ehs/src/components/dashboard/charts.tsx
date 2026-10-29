/**
 * The dashboard's drawings.
 *
 * Still no charting library: everything here is a bar, a ring or a handful of
 * shapes, and these have to print. Each drawing has the same rule as the
 * figures above it — a mark that can be opened opens the records it counted,
 * and a mark that cannot says so by not being a link.
 */
import type { ReactNode } from 'react'
import TabLink from '@/components/TabLink'

export interface CountRow { label: string; count: number; id?: string | null }

export const MONTHS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D']
export const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December']

export function Legend({ items }: { items: readonly { label: string; cls: string }[] }) {
  return (
    <ul className="mt-2.5 flex flex-wrap gap-x-3 gap-y-1">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5 text-[11px] text-neutral-600">
          <span className={`h-2 w-2 rounded-sm ${i.cls}`} aria-hidden />{i.label}
        </li>
      ))}
    </ul>
  )
}

const markLink =
  'rounded-sm hover:bg-neutral-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500'

/** Ranked horizontal bars — the shape of the answer to "which is worst". */
export function Bars({ rows, linkFor }: {
  rows: CountRow[]
  linkFor?: (row: CountRow) => string
}) {
  const max = Math.max(1, ...rows.map((r) => r.count))
  return (
    <ul className="flex flex-col gap-1.5">
      {rows.map((r) => {
        const bar = (
          <>
            <span className="w-28 shrink-0 truncate text-right text-xs text-neutral-600" title={r.label}>
              {r.label}
            </span>
            {/* The bar's track takes what is left after the label and the
                number, so a long label on a phone cannot push it past the edge. */}
            <span className="flex min-w-0 flex-1 items-center gap-2">
              <span className="h-4 rounded-sm bg-primary-500"
                    style={{ width: `${Math.max(3, (r.count / max) * 85)}%` }} />
              <span className="text-xs font-semibold tabular-nums text-neutral-800">{r.count}</span>
            </span>
          </>
        )
        return (
          <li key={r.label}>
            {linkFor ? (
              <TabLink to={linkFor(r)} className={`flex items-center gap-2 py-0.5 ${markLink}`}
                       title={`${r.label}: ${r.count} — open these`}>
                {bar}
              </TabLink>
            ) : (
              <span className="flex items-center gap-2 py-0.5">{bar}</span>
            )}
          </li>
        )
      })}
    </ul>
  )
}

/* Ring segments, in an order that keeps neighbours distinguishable. Past the
   sixth the rest are one grey "Everything else", because a ring of twenty
   slivers answers nothing. */
const RING = [
  { stroke: 'stroke-primary-600', bg: 'bg-primary-600' },
  { stroke: 'stroke-warning-400', bg: 'bg-warning-400' },
  { stroke: 'stroke-danger-500', bg: 'bg-danger-500' },
  { stroke: 'stroke-primary-300', bg: 'bg-primary-300' },
  { stroke: 'stroke-success-500', bg: 'bg-success-500' },
  { stroke: 'stroke-primary-800', bg: 'bg-primary-800' },
]
const RING_REST = { stroke: 'stroke-neutral-300', bg: 'bg-neutral-300' }

/** The circle Kirk asked for in place of bars. */
export function Donut({ rows, linkFor }: {
  rows: CountRow[]
  linkFor?: (row: CountRow) => string
}) {
  const shown = rows.slice(0, RING.length)
  const rest = rows.slice(RING.length)
  const restCount = rest.reduce((n, r) => n + r.count, 0)
  const total = rows.reduce((n, r) => n + r.count, 0)
  const R = 40
  const C = 2 * Math.PI * R
  let at = 0
  const segments = [
    ...shown.map((r, i) => ({ row: r as CountRow | null, n: r.count, ...RING[i] })),
    ...(restCount ? [{ row: null, n: restCount, ...RING_REST }] : []),
  ]
  return (
    <div className="flex flex-wrap items-center gap-4">
      <svg viewBox="0 0 100 100" className="h-32 w-32 shrink-0 -rotate-90" role="img"
           aria-label={segments.map((s) => `${s.row?.label ?? 'Everything else'} ${s.n}`).join(', ')}>
        <circle cx="50" cy="50" r={R} fill="none" strokeWidth="16" className="stroke-neutral-100" />
        {segments.map((s, i) => {
          const len = (s.n / Math.max(total, 1)) * C
          const el = (
            <circle key={i} cx="50" cy="50" r={R} fill="none" strokeWidth="16"
                    className={s.stroke}
                    strokeDasharray={`${len} ${C - len}`} strokeDashoffset={-at}>
              <title>{`${s.row?.label ?? 'Everything else'}: ${s.n}`}</title>
            </circle>
          )
          at += len
          return el
        })}
      </svg>
      <ul className="flex min-w-0 flex-1 flex-col gap-1">
        {segments.map((s, i) => {
          const body = (
            <>
              <span className={`h-2.5 w-2.5 shrink-0 rounded-sm ${s.bg}`} aria-hidden />
              <span className="min-w-0 flex-1 truncate">{s.row?.label ?? `Everything else (${rest.length})`}</span>
              <span className="font-semibold tabular-nums text-neutral-800">{s.n}</span>
              <span className="w-9 text-right tabular-nums text-neutral-400">
                {Math.round((s.n / Math.max(total, 1)) * 100)}%
              </span>
            </>
          )
          return (
            <li key={i} className="text-xs text-neutral-700">
              {s.row && linkFor ? (
                <TabLink to={linkFor(s.row)} className={`flex items-center gap-2 py-0.5 ${markLink}`}
                         title={`${s.row.label}: ${s.n} — open these`}>{body}</TabLink>
              ) : <span className="flex items-center gap-2 py-0.5">{body}</span>}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/* ── The body picture ──────────────────────────────────────────────────────
   Front and back, built from plain shapes so every region is its own element
   with its own count. Region keys are the server's (services/body_map.py). */

type Shape =
  | { k: 'e'; cx: number; cy: number; rx: number; ry: number }
  | { k: 'r'; x: number; y: number; w: number; h: number; r?: number }

const both = (s: Shape[]): Shape[] =>
  s.flatMap((x) => [x, x.k === 'e' ? { ...x, cx: 120 - x.cx } : { ...x, x: 120 - x.x - x.w }])

const LIMBS: Record<string, Shape[]> = {
  shoulder: both([{ k: 'e', cx: 37, cy: 60, rx: 10, ry: 8 }]),
  arm: both([{ k: 'r', x: 26, y: 66, w: 12, h: 32, r: 5 }, { k: 'r', x: 27, y: 108, w: 10, h: 28, r: 4 }]),
  elbow: both([{ k: 'e', cx: 32, cy: 103, rx: 6, ry: 6 }]),
  wrist: both([{ k: 'r', x: 27, y: 137, w: 10, h: 6, r: 2 }]),
  hand: both([{ k: 'e', cx: 32, cy: 152, rx: 7, ry: 10 }]),
  leg: both([{ k: 'r', x: 44, y: 136, w: 15, h: 44, r: 6 }, { k: 'r', x: 45, y: 193, w: 13, h: 38, r: 5 }]),
  knee: both([{ k: 'e', cx: 51.5, cy: 186, rx: 7, ry: 7 }]),
  ankle: both([{ k: 'r', x: 46, y: 232, w: 11, h: 6, r: 2 }]),
  foot: both([{ k: 'e', cx: 51, cy: 244, rx: 9, ry: 5 }]),
  neck: [{ k: 'r', x: 54, y: 42, w: 12, h: 11, r: 3 }],
  head: [{ k: 'e', cx: 60, cy: 24, rx: 16, ry: 19 }],
}
const FRONT: Record<string, Shape[]> = {
  ...LIMBS,
  chest: [{ k: 'r', x: 43, y: 53, w: 34, h: 33, r: 6 }],
  abdomen: [{ k: 'r', x: 45, y: 87, w: 30, h: 29, r: 5 }],
  hip: [{ k: 'r', x: 43, y: 117, w: 34, h: 18, r: 6 }],
  face: [{ k: 'e', cx: 60, cy: 27, rx: 11, ry: 13 }],
  ear: both([{ k: 'e', cx: 43.5, cy: 26, rx: 2.6, ry: 5 }]),
  eye: both([{ k: 'e', cx: 55, cy: 23, rx: 3, ry: 2 }]),
  mouth: [{ k: 'r', x: 55, y: 32, w: 10, h: 3.5, r: 1.5 }],
}
const BACK: Record<string, Shape[]> = {
  ...LIMBS,
  back: [{ k: 'r', x: 43, y: 53, w: 34, h: 63, r: 6 }],
  hip: [{ k: 'r', x: 43, y: 117, w: 34, h: 18, r: 6 }],
  ear: both([{ k: 'e', cx: 43.5, cy: 26, rx: 2.6, ry: 5 }]),
}
// Small features last, so they sit on top of what they are part of.
const ORDER = ['head', 'face', 'ear', 'eye', 'mouth', 'neck', 'shoulder', 'chest', 'abdomen',
  'back', 'hip', 'arm', 'elbow', 'wrist', 'hand', 'leg', 'knee', 'ankle', 'foot']

/* Five steps from "none" to "the most". Steps rather than a continuous ramp:
   the reader can match a shade to the key, which a gradient does not allow. */
const SHADES = ['fill-neutral-100', 'fill-primary-200', 'fill-primary-400', 'fill-primary-600', 'fill-primary-800']
const KEY = ['bg-neutral-100', 'bg-primary-200', 'bg-primary-400', 'bg-primary-600', 'bg-primary-800']

function shadeFor(n: number, max: number): number {
  if (n <= 0) return 0
  return Math.min(4, 1 + Math.floor(((n - 1) / Math.max(max, 1)) * 4))
}

function Figure({ shapes, counts, max, names, title }: {
  shapes: Record<string, Shape[]>
  counts: Record<string, number>
  max: number
  names: Record<string, string>
  title: string
}) {
  return (
    <figure className="flex flex-col items-center">
      <svg viewBox="0 0 120 252" className="h-56 w-auto" role="img"
           aria-label={`${title}: ${ORDER.filter((r) => shapes[r] && counts[r])
             .map((r) => `${names[r] ?? r} ${counts[r]}`).join(', ') || 'no injuries'}`}>
        {ORDER.filter((r) => shapes[r]).map((region) => {
          const n = counts[region] ?? 0
          return (
            <g key={region} className={`${SHADES[shadeFor(n, max)]} stroke-neutral-400`} strokeWidth="0.8">
              <title>{`${names[region] ?? region}: ${n}`}</title>
              {shapes[region].map((s, i) => s.k === 'e'
                ? <ellipse key={i} cx={s.cx} cy={s.cy} rx={s.rx} ry={s.ry} />
                : <rect key={i} x={s.x} y={s.y} width={s.w} height={s.h} rx={s.r ?? 0} />)}
            </g>
          )
        })}
      </svg>
      <figcaption className="mt-1 text-[11px] font-medium uppercase tracking-wide text-neutral-500">
        {title}
      </figcaption>
    </figure>
  )
}

export function BodyMap({ regions, names, rows, offPicture, linkFor }: {
  regions: Record<string, number>
  names: Record<string, string>
  rows: CountRow[]
  offPicture: CountRow[]
  linkFor: (row: CountRow) => string
}) {
  const max = Math.max(1, ...Object.values(regions))
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-center gap-6">
        <Figure shapes={FRONT} counts={regions} max={max} names={names} title="Front" />
        <Figure shapes={BACK} counts={regions} max={max} names={names} title="Back" />
      </div>
      <ul className="flex items-center justify-center gap-1 text-[11px] text-neutral-500" aria-label="Shading key">
        <li>None</li>
        {KEY.map((cls) => <li key={cls} className={`h-2.5 w-5 rounded-sm border border-neutral-200 ${cls}`} />)}
        <li>Most ({max})</li>
      </ul>
      {/* The list stays: the picture shows where, the list is what opens. */}
      <Bars rows={rows} linkFor={linkFor} />
      {offPicture.length > 0 && (
        <p className="text-[11px] leading-relaxed text-neutral-500">
          Not on the picture: {offPicture.map((r) => `${r.label} (${r.count})`).join(', ')}.
          HSE can place a list entry on the picture under Settings → Lists → Body Part.
        </p>
      )}
    </div>
  )
}

/* ── Month by month ────────────────────────────────────────────────────── */

export interface Series<T> { key: keyof T & string; label: string; cls: string }

/** Twelve months, one or two series side by side. */
export function MonthColumns<T extends { month: string }>({ rows, series, linkFor, height = 'h-28', titleFor, stacked = false }: {
  rows: T[]
  series: Series<T>[]
  linkFor?: (row: T, index: number) => string
  height?: string
  titleFor?: (row: T, index: number) => string
  /** One column of parts — for a composition (injuries by severity), where
   *  the month's total is the point. Side by side otherwise. */
  stacked?: boolean
}) {
  const value = (r: T, k: keyof T) => Number(r[k] ?? 0)
  const total = (r: T) => series.reduce((n, s) => n + value(r, s.key), 0)
  const max = Math.max(1, ...rows.flatMap((r) => stacked ? [total(r)] : series.map((s) => value(r, s.key))))
  return (
    <>
      {/* items-stretch: percentage heights need a parent with a definite one. */}
      <div className={`flex ${height} items-stretch gap-1.5`}>
        {rows.map((r, i) => {
          const inner = (
            <>
              {stacked ? (
                <div className="flex w-full flex-1 flex-col justify-end">
                  <div className="flex w-full flex-col-reverse overflow-hidden rounded-sm"
                       style={{ height: `${(total(r) / max) * 100}%` }}>
                    {series.map((s) => value(r, s.key) > 0 && (
                      <div key={s.key} className={s.cls}
                           style={{ height: `${(value(r, s.key) / Math.max(total(r), 1)) * 100}%` }} />
                    ))}
                  </div>
                </div>
              ) : (
                <div className="flex w-full flex-1 items-end gap-px">
                  {series.map((s) => (
                    <div key={s.key} className={`flex-1 rounded-sm ${s.cls}`}
                         style={{ height: `${(value(r, s.key) / max) * 100}%` }} />
                  ))}
                </div>
              )}
              <span className="text-[11px] text-neutral-400">{MONTHS[i]}</span>
            </>
          )
          const title = titleFor?.(r, i) ?? `${MONTH_NAMES[i]}: ${series
            .map((s) => `${s.label} ${value(r, s.key)}`).join(', ')}`
          return linkFor ? (
            <TabLink key={r.month} to={linkFor(r, i)} title={`${title} — open these`}
                     className={`flex flex-1 flex-col items-center gap-1.5 ${markLink}`}>{inner}</TabLink>
          ) : (
            <div key={r.month} title={title} className="flex flex-1 flex-col items-center gap-1.5">{inner}</div>
          )
        })}
      </div>
      {series.length > 1 && <Legend items={series} />}
    </>
  )
}

/** Columns of stacked parts, one per supervisor. */
export function StackedColumns({ columns, parts, empty }: {
  columns: { key: string; name: string; values: Record<string, number> }[]
  parts: { key: string; label: string; cls: string }[]
  empty: ReactNode
}) {
  const totals = columns.map((c) => parts.reduce((n, p) => n + (c.values[p.key] ?? 0), 0))
  const max = Math.max(1, ...totals)
  if (columns.length === 0) return <>{empty}</>
  return (
    <>
      <div className="overflow-x-auto">
        {/* Wide enough for every column, scrolling inside the panel past that;
            a column never wider than a label needs, however few there are. */}
        <div className="flex h-40 min-w-full items-stretch justify-center gap-3"
             style={{ width: `${columns.length * 64}px` }}>
          {columns.map((c, i) => (
            <div key={c.key} className="flex w-12 max-w-[104px] flex-1 flex-col items-center gap-1"
                 title={`${c.name}: ${parts.map((p) => `${p.label} ${c.values[p.key] ?? 0}`).join(', ')}`}>
              <span className="text-[11px] font-semibold tabular-nums text-neutral-700">{totals[i]}</span>
              <div className="flex w-full flex-1 flex-col justify-end">
                <div className="flex w-full flex-col-reverse overflow-hidden rounded-sm"
                     style={{ height: `${(totals[i] / max) * 100}%` }}>
                  {parts.map((p) => (c.values[p.key] ?? 0) > 0 && (
                    <div key={p.key} className={p.cls}
                         style={{ height: `${((c.values[p.key] ?? 0) / Math.max(totals[i], 1)) * 100}%` }} />
                  ))}
                </div>
              </div>
              <span className="w-full truncate text-center text-[11px] text-neutral-500">{c.name}</span>
            </div>
          ))}
        </div>
      </div>
      <Legend items={parts} />
    </>
  )
}
