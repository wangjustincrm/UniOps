import { createContext } from 'react'
import type { TourStep } from './types'

/** One part that is a tour stop: its help id (its text and its anchor), its slot (its place), its key
 *  (one stop per key: e.g. a list's many status badges are one stop, the first shown). */
export interface Stop { help: string; slot: string; key: string; el: HTMLElement }

/** A page's or a record's own stops (TourScope). */
export interface ScopeValue {
  id: string
  kind: 'page' | 'record'
  add: (stop: Stop) => () => void
  stops: () => Stop[]
}
export const ScopeContext = createContext<ScopeValue | null>(null)

/** The one manager (TourManager): the slot order, the one tour on screen, the "?" key. */
export interface ManagerValue {
  order: (slot: string) => number
  start: (steps: TourStep[], container?: Element | null, onEnd?: () => void) => void
  registerHelp: (h: { trigger: () => HTMLElement | null; open: () => void }) => () => void
}
export const ManagerContext = createContext<ManagerValue | null>(null)
