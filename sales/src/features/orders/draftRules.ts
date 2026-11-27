export const OPERATE = 'sales.order.operate'
// What a refused submit says (user 2026-11-13: a manual order needs its carrier before it goes).
export const SUBMIT_ERRORS: Record<string, string> = {
  'order.carrier': 'Choose how it ships first: Edit draft → Ships by.',
  'order.carrier_express': 'Purolator Express is not offered: Edit draft → choose Purolator or LTL.',
  'order.lines_invalid': 'Fix every line first: Edit draft.',
}
