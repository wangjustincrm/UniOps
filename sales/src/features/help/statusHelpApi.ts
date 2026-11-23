import { api } from '@/lib/api'

type Texts = Record<string, { body: string }>
/** GET /help/texts (sales-api app/domain/status_help.py): {statuses | holds | details | payments | terms: {label: {body}}} (terms: app/domain/glossary.py). */
export interface StatusHelpRead { statuses: Texts; holds: Texts; details: Texts; payments: Texts; terms: Texts }

export const fetchStatusHelp = () => api.get<StatusHelpRead>('/help/texts')
