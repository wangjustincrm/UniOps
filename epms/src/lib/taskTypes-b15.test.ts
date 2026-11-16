import { describe, expect, it } from 'vitest'
import { isRetitledTask, taskCardLabel } from './taskTypes'

describe('taskCardLabel — retitled tasks (ISS-0230, ISS-0259)', () => {
  it('shows "Awaiting credit note — INV-…" instead of the type label', () => {
    const t = { type: 'resolve_exception', title: 'Awaiting credit note — INV-2026-0862' }
    expect(isRetitledTask(t)).toBe(true)
    expect(taskCardLabel(t)).toBe('Awaiting credit note — INV-2026-0862')
  })

  it('shows the re-match title for a rejected delegated match', () => {
    const t = { type: 'match_invoice', title: 'Re-match invoice INV-2026-0883' }
    expect(isRetitledTask(t)).toBe(true)
    expect(taskCardLabel(t)).toBe('Re-match invoice INV-2026-0883')
  })

  it('keeps the type label for a first assignment or a new exception', () => {
    expect(taskCardLabel({ type: 'match_invoice', title: 'Match invoice INV-2026-0883' })).toBe('Match Invoice')
    expect(taskCardLabel({ type: 'resolve_exception', title: 'Resolve exception INV-1' })).toBe('Resolve Match Exception')
    expect(isRetitledTask({ type: 'create_pa', title: 'Re-match something' })).toBe(false)
  })
})
