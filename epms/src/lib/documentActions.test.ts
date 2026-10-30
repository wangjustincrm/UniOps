import { describe, expect, it } from 'vitest'
import { canCancelGr, canRecallDocument, canWithdrawDocument } from './documentActions'

const CREATOR = 'u-creator'
const creator = { id: CREATOR, role: 'procurement_officer' }
const admin = { id: 'u-admin', role: 'system_admin' }
const bystander = { id: 'u-other', role: 'procurement_officer' }

describe('canWithdrawDocument', () => {
  // ISS-0082: a returned PO offered no Withdraw although the engine cancels it.
  it.each(['draft', 'returned', 'submitted'])('offers a %s PO to its creator and System Admin', (status) => {
    const po = { status, created_by: CREATOR }
    expect(canWithdrawDocument('po', po, creator)).toBe(true)
    expect(canWithdrawDocument('po', po, admin)).toBe(true)
  })

  it('does not offer it to someone the engine would refuse', () => {
    // The old gate was "any procurement role" — a colleague got a button
    // that could only end in "Not authorized to cancel".
    expect(canWithdrawDocument('po', { status: 'returned', created_by: CREATOR }, bystander)).toBe(false)
  })

  it('offers a submitted PO to the approver holding it', () => {
    expect(canWithdrawDocument('po', { status: 'submitted', created_by: CREATOR }, bystander, true)).toBe(true)
  })

  it.each(['in_review', 'approved', 'issued', 'rejected', 'cancelled'])('never offers a %s PO', (status) => {
    expect(canWithdrawDocument('po', { status, created_by: CREATOR }, admin, true)).toBe(false)
  })

  it('offers a PA only while draft or returned', () => {
    expect(canWithdrawDocument('pa', { status: 'returned', created_by: CREATOR }, creator)).toBe(true)
    expect(canWithdrawDocument('pa', { status: 'draft', created_by: CREATOR }, creator)).toBe(true)
    expect(canWithdrawDocument('pa', { status: 'submitted', created_by: CREATOR }, admin, true)).toBe(false)
  })
})

describe('canRecallDocument', () => {
  it.each(['submitted', 'in_review'])('offers a %s document to its creator and System Admin', (status) => {
    expect(canRecallDocument({ status, created_by: CREATOR }, creator)).toBe(true)
    expect(canRecallDocument({ status, created_by: CREATOR }, admin)).toBe(true)
  })

  it('does not offer it to anyone else', () => {
    expect(canRecallDocument({ status: 'submitted', created_by: CREATOR }, bystander)).toBe(false)
    expect(canRecallDocument({ status: 'submitted', created_by: null }, { id: null, role: 'requester' })).toBe(false)
  })

  it.each(['draft', 'returned', 'approved', 'active', 'cancelled'])('never offers a %s document', (status) => {
    expect(canRecallDocument({ status, created_by: CREATOR }, admin)).toBe(false)
  })
})

describe('canCancelGr', () => {
  // ISS-0124 / PRD GR-C-004.
  it.each(['pending_ack', 'collection_pending'])('offers a %s GR', (status) => {
    expect(canCancelGr({ status, created_by: CREATOR }, creator, false)).toBe(true)
    expect(canCancelGr({ status, created_by: CREATOR }, admin, false)).toBe(true)
    expect(canCancelGr({ status, created_by: CREATOR }, bystander, true)).toBe(true)
  })

  it('does not offer it to a viewer who neither recorded it nor receives goods', () => {
    expect(canCancelGr({ status: 'pending_ack', created_by: CREATOR }, bystander, false)).toBe(false)
  })

  it.each(['collected', 'confirmed', 'discrepancy', 'cancelled'])('never offers a %s GR', (status) => {
    expect(canCancelGr({ status, created_by: CREATOR }, admin, true)).toBe(false)
  })
})
