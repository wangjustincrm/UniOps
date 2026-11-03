import type { RecordTab } from '@/platform-ui'
import { AuditHistory, type AuditEntry } from './AuditHistory'

/** A record sheet's History tab (who changed what and when). */
export const historyTab = (resource: string, id: number, version: number | undefined,
  load: (id: number) => Promise<AuditEntry[]>): RecordTab => ({
  id: 'history', label: 'History', render: () => <AuditHistory resource={resource} id={id} version={version} load={load} />,
})
