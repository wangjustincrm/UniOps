import { describe, expect, it } from 'vitest'
import { TASK_TYPE_LABELS, isRetitledTask, taskCardLabel, taskGroupType } from './taskTypes'

// epms-api crud/gr.py _create_ack_task titles a service GR's acknowledge_gr task so.
const serviceAck = { type: 'acknowledge_gr', title: 'Confirm Service Completion: GR-20261105-0019 — Cleaning' }
const physicalAck = { type: 'acknowledge_gr', title: 'Acknowledge GR: GR-20261105-0020 — Tape' }

describe('service GR confirmation task (ISS-0179)', () => {
  it('is titled and grouped as a service confirmation, not Acknowledge Goods Receipt', () => {
    expect(isRetitledTask(serviceAck)).toBe(true)
    expect(taskCardLabel(serviceAck)).toBe('Confirm Service Completion: GR-20261105-0019 — Cleaning')
    expect(taskGroupType(serviceAck)).toBe('confirm_service_gr')
    expect(TASK_TYPE_LABELS[taskGroupType(serviceAck)]).not.toBe('Acknowledge Goods Receipt')
  })

  it('leaves a physical GR acknowledgement as it was', () => {
    expect(isRetitledTask(physicalAck)).toBe(false)
    expect(taskCardLabel(physicalAck)).toBe('Acknowledge Goods Receipt')
    expect(taskGroupType(physicalAck)).toBe('acknowledge_gr')
    expect(taskGroupType({ type: 'collect_goods', title: 'Collect Goods: GR-1' })).toBe('collect_goods')
  })
})
