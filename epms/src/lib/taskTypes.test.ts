/**
 * taskHref: VMS-owned tasks must leave EPMS.
 *
 * vms-api writes into the SHARED `tasks` table, so its rows reach the EPMS Task
 * Inbox — but EPMS has no page for a visit or a visitor. Before this, HREF_MAP
 * had no vms_* entry and the fallback produced a bare "/<uuid>": every VMS card
 * in the EPMS inbox was a dead link.
 */
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/stores/auth.store', () => ({
  // No session → the handoff helpers return the bare URL (no #__session), which
  // keeps these assertions about ROUTING, not about token encoding.
  useAuthStore: { getState: () => ({ token: null, refreshToken: null, user: null }) },
}))

import { TASK_TYPE_LABELS, taskHref } from './taskTypes'
import { VMS_URL } from './api'

const VISIT_ID = '11111111-1111-4111-8111-111111111111'
const VISITOR_ID = '22222222-2222-4222-8222-222222222222'

describe('taskHref — VMS doc types', () => {
  it('routes visit-anchored tasks to the VMS visit page', () => {
    for (const type of ['check_out_visitor', 'prepare_ppe', 'approve_visit']) {
      const href = taskHref({ type, document_type: 'vms_visit', document_id: VISIT_ID })
      expect(href).toBe(`${VMS_URL}/${VISIT_ID}`)
    }
  })

  it('routes compliance tasks to the visitor compliance page', () => {
    // document_id is the VISITOR id for these two (services/compliance.py).
    for (const docType of ['vms_train', 'vms_ppe']) {
      const href = taskHref({ type: 'x', document_type: docType, document_id: VISITOR_ID })
      expect(href).toBe(`${VMS_URL}/visitor/${VISITOR_ID}/compliance`)
    }
  })

  it('never emits the bare "/<id>" fallback for a VMS doc type', () => {
    for (const docType of ['vms_visit', 'vms_train', 'vms_ppe']) {
      const href = taskHref({ type: 'x', document_type: docType, document_id: VISIT_ID })
      expect(href.startsWith(VMS_URL)).toBe(true)
      expect(href).not.toBe(`/${VISIT_ID}`)
    }
  })
})

describe('taskHref — EPMS doc types still resolve in-app', () => {
  it('keeps PO/PR/agreement routes relative', () => {
    expect(taskHref({ type: 'approve_po', document_type: 'po', document_id: VISIT_ID }))
      .toBe(`/po/${VISIT_ID}`)
    // 协议:HREF_MAP 里已有 agr,催票任务(chase_agreement_invoice)靠它落地。
    expect(taskHref({ type: 'chase_agreement_invoice', document_type: 'agr', document_id: VISIT_ID }))
      .toBe(`/agreements/${VISIT_ID}`)
  })

  it('sends create_pa to the prefilled PA create page', () => {
    expect(taskHref({ type: 'create_pa', document_type: 'po', document_id: VISIT_ID }))
      .toBe(`/pa/create?poId=${VISIT_ID}`)
  })
})

describe('taskHref — PO sign-off', () => {
  const PO_ID = '33333333-3333-4333-8333-333333333333'

  it('routes a sign-off task to the PO it anchors on', () => {
    // document_type is 'posign', not 'po' — the sign-off is a separate workflow
    // over the same row, so its tasks and the PO's own tasks stay apart. The
    // card still has to open the PO.
    for (const type of ['sign_po', 'revise_po_signoff']) {
      expect(taskHref({ type, document_type: 'posign', document_id: PO_ID }))
        .toBe(`/po/${PO_ID}`)
    }
  })

  it('does not fall through to the dead bare-uuid link', () => {
    const href = taskHref({ type: 'sign_po', document_type: 'posign', document_id: PO_ID })
    expect(href).not.toBe(`/${PO_ID}`)
  })
})

describe('TASK_TYPE_LABELS — PO sign-off', () => {
  it('labels both sign-off task types', () => {
    // The inbox falls back to the raw type string, so a missing label shows
    // "sign_po" on the card rather than failing loudly.
    expect(TASK_TYPE_LABELS.sign_po).toBe('Sign Purchase Order')
    expect(TASK_TYPE_LABELS.revise_po_signoff).toBe('Revise PO Sign-off')
  })
})
