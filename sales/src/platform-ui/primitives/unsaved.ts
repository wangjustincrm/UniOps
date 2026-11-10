import { createContext, createElement, useContext, useEffect, useState, type ReactNode } from 'react'

/** Forms with changes not saved yet, per place that can drop them (2026-11-10). A sheet, a dialog,
 *  a record sheet's tab and a list's open record each own a scope: leaving one asks only about the
 *  forms inside it (and in scopes nested inside it), never about a form behind it. A form reports
 *  itself to its nearest scope and every scope around it. */
export interface UnsavedScope {
  readonly own: Set<symbol>
  readonly parent: UnsavedScope | null
}

const ROOT: UnsavedScope = { own: new Set(), parent: null }
const Scope = createContext<UnsavedScope>(ROOT)

/** A new scope inside the current one (or inside `parent`); hand it to <UnsavedBoundary> and to confirmDiscard. */
export function useUnsavedScope(parent?: UnsavedScope): UnsavedScope {
  const around = useContext(Scope)
  const [scope] = useState<UnsavedScope>(() => ({ own: new Set(), parent: parent ?? around }))
  return scope
}

/** The scope a component is in (what leaving from here would drop). */
export const useCurrentUnsavedScope = (): UnsavedScope => useContext(Scope)

/** The forms below report to `scope`. */
export function UnsavedBoundary({ scope, children }: { scope: UnsavedScope; children?: ReactNode }) {
  return createElement(Scope.Provider, { value: scope }, children)
}

export function useReportUnsaved(isDirty: boolean): void {
  const scope = useContext(Scope)
  useEffect(() => {
    if (!isDirty) return undefined
    const id = Symbol('unsaved')
    const chain: UnsavedScope[] = []
    for (let s: UnsavedScope | null = scope; s; s = s.parent) chain.push(s)
    chain.forEach((s) => s.own.add(id))
    return () => { chain.forEach((s) => s.own.delete(id)) }
  }, [isDirty, scope])
}

/** True when nothing inside `scope` is lost by leaving it: nothing unsaved, or the person agreed to
 *  drop it. Leaving then unmounts those forms, which removes them. Without a scope: the whole app. */
export function confirmDiscard(scope: UnsavedScope = ROOT): boolean {
  if (scope.own.size === 0) return true
  return window.confirm('You have changes that are not saved. Discard them?')
}

/** Whether anything inside `scope` is unsaved (a test, or a "Saved" indicator). */
export const hasUnsaved = (scope: UnsavedScope = ROOT) => scope.own.size > 0
