import { describe, expect, it } from 'vitest'
import {
  EXCEL_MESSAGE, HOST_KEY_MESSAGE, SECRET_NAME_MESSAGE, connectionCreateSchema, connectionEmptyValues, connectionFields, connectionUpdateSchema,
} from './connectionSchema'

const valid = { ...connectionEmptyValues, name: 'radiant', host: 'sftp-test', username: 'radiant', inbound_folder: '/in',
  secret_name: 'SALES_SECRET_SFTP_TEST' }
const issues = (r: { error?: { issues: { path: PropertyKey[]; message: string }[] } }) =>
  Object.fromEntries((r.error?.issues ?? []).map((i) => [i.path.join('.'), i.message]))

describe('connection form schema', () => {
  it('accepts a complete SFTP connection (the positive half)', () => {
    expect(connectionCreateSchema.safeParse(valid).success).toBe(true)
  })
  it('an SFTP connection needs host, user, inbound folder and secret name', () => {
    const r = connectionCreateSchema.safeParse({ ...valid, host: '', username: ' ', inbound_folder: null, secret_name: '' })
    expect(issues(r)).toEqual({ host: 'Required for an SFTP connection', username: 'Required for an SFTP connection',
      inbound_folder: 'Required for an SFTP connection', secret_name: 'Required for an SFTP connection' })
  })
  it('MOVE needs a processed folder; the secret is a variable name, never a password', () => {
    expect(issues(connectionUpdateSchema.safeParse({ ...valid, after_read: 'MOVE' }))).toEqual({ processed_folder: 'Required when after-read is MOVE' })
    expect(issues(connectionCreateSchema.safeParse({ ...valid, secret_name: 'hunter2!' }))).toEqual({ secret_name: SECRET_NAME_MESSAGE })
  })
  it('host_key takes only a public key line; a token never; SHOPIFY none; Excel numbers refused', () => {
    expect(issues(connectionCreateSchema.safeParse({ ...valid, host_key: 'shpat_0123456789abcdef' }))).toEqual({ host_key: HOST_KEY_MESSAGE })
    expect(issues(connectionCreateSchema.safeParse({ ...valid, host_key: 'correct horse battery' }))).toEqual({ host_key: HOST_KEY_MESSAGE })
    expect(connectionCreateSchema.safeParse({ ...valid, host_key: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOYyzHbjnpUgsd3f+LSfKEJkQUehyBzwkRpZ0hBihPDh' }).success).toBe(true)
    expect(issues(connectionCreateSchema.safeParse({ ...valid, kind: 'SHOPIFY', host_key: 'ssh-ed25519 AAAA' }))).toMatchObject({ host_key: 'host_key must be empty for a SHOPIFY connection' })
    expect(issues(connectionCreateSchema.safeParse({ ...valid, username: '1.22E+11' }))).toEqual({ username: EXCEL_MESSAGE })
  })

  it('every schema field has an input and there is no password input', () => {
    const names = connectionFields.map((f) => f.name)
    for (const k of Object.keys(connectionCreateSchema.parse(valid))) expect(names).toContain(k)
    expect(names.some((n) => /password/i.test(n))).toBe(false)
  })
})
