import { createContext, useContext } from 'react'
import type { QueryKey } from '@tanstack/react-query'
import type { z } from 'zod'
import type { FieldValues } from 'react-hook-form'
import type { HelpId } from '../help'
import type { ActionRunBody, ActionOut } from '../resource/ResourceClient'
import { PermissionContext } from './permissions'
import type { FieldSpec } from './SchemaForm'

/** Where an action shows: the row's "⋯" menu, the selection bar, the record's header (its sheet and its tab: the
 *  action bar, actionBar.ts). */
export type ActionPlacement = 'row' | 'bulk' | 'header'

/** What the person gives an action: a form (SchemaForm fields), or only a reason. None: a plain confirm. */
export type ActionInput =
  | { schema: z.ZodType<FieldValues, FieldValues>; fields: FieldSpec[]; defaults: () => Record<string, unknown>; toBody?: (v: Record<string, unknown>) => unknown }
  | { reason: { label: string } }

/** THE record action (user 2026-11-23): defined once — its words, permission, rule, form and where it shows — and
 *  rendered by the row menu, the selection bar and the record sheet. The server (POST <list>/actions/<id>) checks
 *  the permission and decides which records it fits: `eligible` here is a hint for the counts and the greyed
 *  items only. */
export interface RecordAction<T> {
  /** The server's action id (platform/bulk_actions.py registry). */
  id: string
  label: string
  /** Shown only to those who hold it (hidden, never greyed). */
  permission: string
  /** 'group': also on a selection; 'single': one record at a time. */
  scope: 'single' | 'group'
  placement: ActionPlacement[]
  tone?: 'default' | 'destructive'
  /** true, or why it does not fit this record (the greyed item's reason). */
  eligible?: (row: T) => true | string
  /** Not fitting a record: greyed with why (default) where the person would expect it, or hidden (e.g. one of a pair
   *  like Mark settled / Undo settled: only the one for the record's state shows). */
  whenNotFit?: 'greyed' | 'hidden'
  /** A button in the record's header when it fits: the lower, the more important (at most two show). None: in "⋯". */
  primary?: number
  /** Its place in the header's "⋯" (default: as declared). */
  order?: number
  input?: ActionInput
  /** The dialog's title for n records (default: the label and the count). */
  title?: (n: number) => string
  description?: string
  /** What the status row says when n were done, e.g. n => `${n} marked settled`. */
  done: (n: number) => string
  /** What the preview says of the n that will be done, e.g. n => `${n} will be marked settled`. */
  will: (n: number) => string
  help?: HelpId
  /** What is read again after it ran (default: the list it is in), e.g. the records of another list it changes. */
  invalidates?: QueryKey[]
}

export function defineActions<T>(actions: RecordAction<T>[]): RecordAction<T>[] {
  return actions
}

/** The actions this person may use, for one placement. */
export function useActionsFor<T>(actions: RecordAction<T>[] | undefined, placement: ActionPlacement): RecordAction<T>[] {
  const can = useContext(PermissionContext)
  return (actions ?? []).filter((a) => a.placement.includes(placement) && can(a.permission)
    && (placement !== 'bulk' || a.scope === 'group'))
}

export const fits = <T,>(a: RecordAction<T>, row: T): true | string => a.eligible?.(row) ?? true

/** Whether it shows for this record at all: a hidden-when-not-fitting action that does not fit, does not. */
export const showsFor = <T,>(a: RecordAction<T>, row: T): boolean => a.whenNotFit !== 'hidden' || fits(a, row) === true

/** Who runs an action: the list or sheet it is in (its resource) and how a record is named in a result. */
export interface ActionHost<T> {
  run: (actionId: string, body: ActionRunBody) => Promise<ActionOut>
  name: (row: T) => string
  /** What the action sends out of the company (the server's words, /meta capabilities), if anything. */
  sends?: (actionId: string) => string | undefined
}
export const ActionHostContext = createContext<ActionHost<unknown> | null>(null)
export function useActionHost<T>(): ActionHost<T> | null {
  return useContext(ActionHostContext) as ActionHost<T> | null
}

const SAID = 5  // notes said by name in the status row; the rest counted

/** The status row's words for a run: done, skipped, failed (with each failure's record and reason). */
export function resultText(a: { done: (n: number) => string }, out: ActionOut, nameOf: (id: number) => string): string {
  const { done, skipped, failed } = out.counts
  const fails = out.results.filter((r) => r.status === 'failed').map((r) => `${nameOf(r.id)}: ${r.reason ?? 'failed'}`)
  const notes = out.results.filter((r) => r.status === 'done' && (r.problem || r.note)).map((r) => `${nameOf(r.id)}: ${r.problem ?? r.note}`)
  // One record: the person must see which happened (left out and why, or failed), not a count.
  const why = out.results.length === 1 && skipped ? ` (${out.results[0].reason ?? 'left out'})` : ''
  return [a.done(done), skipped ? `${skipped} skipped${why}` : '', failed ? `${failed} failed (${fails.join('; ')})` : '', notes.slice(0, SAID).join('; ') + (notes.length > SAID ? `; and ${notes.length - SAID} more` : '')]
    .filter(Boolean).join(' · ')
}
