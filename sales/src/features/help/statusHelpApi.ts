import { api } from '@/lib/api'

type Texts = Record<string, { body: string }>
/** GET /help/order-statuses (sales-api app/domain/status_help.py): {statuses | holds | details: {label: {body}}}. */
export interface StatusHelpRead { statuses: Texts; holds: Texts; details: Texts }

export const fetchStatusHelp = () => api.get<StatusHelpRead>('/help/order-statuses')
