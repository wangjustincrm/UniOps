import type { ConnectionRead } from './connectionsApi'

export type HealthState = 'inactive' | 'login_refused' | 'key_not_pinned' | 'secret_missing' | 'last_run_failed' | 'last_run_partial' | 'ready'

export interface ConnectionHealth {
  state: HealthState
  label: string
  tone: 'neutral' | 'success' | 'warning' | 'danger'
  /** What is wrong and what to do, in plain words. */
  tip: string
}

/** THE connection's health, the first thing that needs a person: for the list's
 *  column, the sheet's facts and its Overview. */
export function connectionHealth(c: ConnectionRead): ConnectionHealth {
  if (!c.active) return { state: 'inactive', label: 'Inactive', tone: 'neutral', tip: 'This connection is switched off and is not read.' }
  if (c.auth_failed_at) {
    return { state: 'login_refused', label: 'Login refused', tone: 'danger',
      tip: 'The server refused the login, so this connection is skipped. Fix the login, then use Clear on its Overview.' }
  }
  if (c.kind === 'SFTP' && !c.host_key) {
    return { state: 'key_not_pinned', label: 'Key not pinned', tone: 'warning',
      tip: 'The server is not trusted yet, so it is not read. Open it, test the connection and pin its key.' }
  }
  if (!c.secret_present) {
    return { state: 'secret_missing', label: 'Secret missing', tone: 'warning',
      tip: 'The password is not on the server yet. Ask your administrator to add it under the name on this connection.' }
  }
  if (c.last_run_status === 'FAILED') {
    return { state: 'last_run_failed', label: 'Last run failed', tone: 'danger',
      tip: `${c.last_error ?? c.last_run_summary ?? 'The last run failed.'} See its Schedule tab, then Run now.` }
  }
  if (c.last_run_status === 'PARTIAL') {
    return { state: 'last_run_partial', label: 'Last run partial', tone: 'warning',
      tip: `${c.last_run_summary ?? 'Some files failed.'} See its files and runs.` }
  }
  return { state: 'ready', label: 'Ready', tone: 'success', tip: 'Nothing needs doing.' }
}
