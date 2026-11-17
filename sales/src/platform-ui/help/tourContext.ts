import { createContext } from 'react'
import type { SlotDef } from './tourOrder'
import type { TourStep } from './types'

/** One part that is a tour stop: its help id (its text and its anchor), its slot (its place), its key
 *  (one stop per key: e.g. a list's many status badges are one stop, the first shown). */
export interface Stop { help: string; slot: string; key: string; el: HTMLElement; text?: string }

/** A page's or a record's own stops (TourScope). */
export interface ScopeValue {
  id: string
  kind: 'page' | 'record'
  add: (stop: Stop) => () => void
  stops: () => Stop[]
  /** Told (once per frame) when a stop comes or goes; `version` counts the changes. */
  subscribe: (listener: () => void) => () => void
  version: () => number
}
export const ScopeContext = createContext<ScopeValue | null>(null)

/** The one manager (TourManager): the slot order, the one tour on screen, the "?" key. */
export interface ManagerValue {
  order: (slot: string) => number
  /** A slot's place, group and version (the table with the module's overrides). */
  slot: (slot: string) => SlotDef | undefined
  /** Every slot (the tips catalog). */
  slots: () => [string, SlotDef][]
  /** The app's name, for "All <app> tips". */
  appName?: string
  start: (steps: TourStep[], container?: Element | null, onEnd?: () => void) => void
  registerHelp: (h: { trigger: () => HTMLElement | null; open: () => void }) => () => void
}
export const ManagerContext = createContext<ManagerValue | null>(null)
