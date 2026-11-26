/** The periods a date search may name (#32), as the server's list API has them (a server test holds the two equal): kept as
 *  the token in a saved view; a chip shows the words. The server resolves them on its business day. */
export const RELATIVE_DATES = [
  { value: 'today', label: 'Today', help: 'The business day (Toronto).' },
  { value: 'last_7_days', label: 'Last 7 days', help: 'Today and the 6 days before.' },
  { value: 'last_30_days', label: 'Last 30 days', help: 'Today and the 29 days before.' },
  { value: 'this_month', label: 'This month', help: 'From the 1st of this month up to today.' },
  { value: 'last_month', label: 'Last month', help: 'The whole month before this one.' },
] as const

export type RelativeDate = (typeof RELATIVE_DATES)[number]['value']

export const relativeDate = (v: unknown) => RELATIVE_DATES.find((r) => r.value === v)
