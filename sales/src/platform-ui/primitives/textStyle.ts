// The few looks text has (2026-11-18): defined once, used by Text and the layout boxes, never written by a page.
export const TONE = {
  default: '', strong: 'text-neutral-900', body: 'text-neutral-800', secondary: 'text-neutral-700', soft: 'text-neutral-600',
  muted: 'text-neutral-500', faint: 'text-neutral-400',
  danger: 'text-danger-600', dangerStrong: 'text-danger-700', warning: 'text-warning-700', success: 'text-success-700', primary: 'text-primary-700',
} as const
export type TextTone = keyof typeof TONE
export const SIZE = { xs: 'text-xs', sm: 'text-sm', base: 'text-base' } as const
export const WEIGHT = { medium: 'font-medium', semibold: 'font-semibold' } as const
export const WRAP = { break: 'break-all', words: 'break-words', nowrap: 'whitespace-nowrap', clamp1: 'line-clamp-1', clamp2: 'line-clamp-2', truncate: 'truncate' } as const
