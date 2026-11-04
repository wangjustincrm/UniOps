import { describe, expect, it } from 'vitest'
import { connectionHealth } from './connectionHealth'
import { scheduleLabel, scheduleSentence, type ConnectionRead } from './connectionsApi'

const ready = { active: true, kind: 'SFTP', host_key: 'ssh-ed25519 AAAA', secret_present: true, auth_failed_at: null,
  last_run_status: 'SUCCEEDED', last_error: null, last_run_summary: null } as ConnectionRead
const state = (over: Partial<ConnectionRead>) => connectionHealth({ ...ready, ...over })

describe('connectionHealth', () => {
  it('each state, with its tone', () => {
    expect(state({})).toMatchObject({ state: 'ready', label: 'Ready', tone: 'success' })
    expect(state({ active: false })).toMatchObject({ state: 'inactive', tone: 'neutral' })
    expect(state({ auth_failed_at: '2026-11-02T20:00:00Z' })).toMatchObject({ state: 'login_refused', tone: 'danger' })
    expect(state({ host_key: null })).toMatchObject({ state: 'key_not_pinned', tone: 'warning' })
    expect(state({ secret_present: false })).toMatchObject({ state: 'secret_missing', tone: 'warning' })
    expect(state({ last_run_status: 'FAILED', last_error: 'timed out' })).toMatchObject({ state: 'last_run_failed', tone: 'danger' })
    expect(state({ last_run_status: 'PARTIAL' })).toMatchObject({ state: 'last_run_partial', tone: 'warning' })
  })
  it('a Shopify connection needs no key', () => {
    expect(state({ kind: 'SHOPIFY', host_key: null }).state).toBe('ready')
  })
  it('the first thing needing a person wins', () => {
    expect(state({ active: false, auth_failed_at: 'x', host_key: null }).state).toBe('inactive')
    expect(state({ auth_failed_at: 'x', host_key: null, secret_present: false }).state).toBe('login_refused')
    expect(state({ host_key: null, secret_present: false, last_run_status: 'FAILED' }).state).toBe('key_not_pinned')
    expect(state({ secret_present: false, last_run_status: 'FAILED' }).state).toBe('secret_missing')
  })
  it('the tip says what to do, in plain words', () => {
    for (const over of [{ auth_failed_at: 'x' }, { host_key: null }, { secret_present: false }, { last_run_status: 'FAILED' }]) {
      expect(state(over).tip).not.toMatch(/host_key|secret_name|SALES_/)
    }
    expect(state({ last_run_status: 'FAILED', last_error: 'timed out' }).tip).toContain('timed out')
  })
})

describe('the schedule in words', () => {
  const every5 = { schedule_minutes: 5, active_from: '06:00:00', active_to: '22:00:00', active_days: 'Mon,Tue,Wed,Thu,Fri', schedule_paused: false }
  it('the list: a phrase, or Paused', () => {
    expect(scheduleLabel(every5)).toBe('Every 5 min · Mon–Fri 06:00–22:00')
    expect(scheduleLabel({ ...every5, active_days: null, active_from: null, active_to: null })).toBe('Every 5 min')
    expect(scheduleLabel({ ...every5, schedule_paused: true })).toBe('Paused')
  })
  it('the form: a sentence from its values', () => {
    expect(scheduleSentence({ schedule_minutes: 5, active_days: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'], active_from: '06:00', active_to: '22:00' }))
      .toBe('Runs every 5 minutes, Mon–Fri, 06:00–22:00 Toronto')
    expect(scheduleSentence({ schedule_minutes: '1', active_days: [], active_from: '', active_to: '' })).toBe('Runs every minute, every day, all day')
    expect(scheduleSentence({ schedule_minutes: 5, schedule_paused: true })).toBe('Paused: it runs only with Run now.')
    expect(scheduleSentence({ schedule_minutes: '' })).toBe('Choose how often it runs.')
  })
})
