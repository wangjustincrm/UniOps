/** Widths: md for a form, lg / xl for a record with tables, full for the whole screen. */
export const SHEET_SIZES = {
  md: 'max-w-xl',
  lg: 'max-w-4xl',
  xl: 'max-w-6xl',
  full: 'max-w-none',
} as const
export type SheetSize = keyof typeof SHEET_SIZES
