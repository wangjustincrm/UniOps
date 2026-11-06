import { renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { confirmDiscard, useReportUnsaved } from './unsaved'

describe('unsaved changes', () => {
  afterEach(() => vi.restoreAllMocks())

  it('asks nothing when nothing is unsaved', () => {
    const ask = vi.spyOn(window, 'confirm')
    expect(confirmDiscard()).toBe(true)
    expect(ask).not.toHaveBeenCalled()
  })

  it('keeps the form when the person says no, drops it when they say yes', () => {
    const { unmount } = renderHook(() => useReportUnsaved(true))
    vi.spyOn(window, 'confirm').mockReturnValueOnce(false)
    expect(confirmDiscard()).toBe(false)
    vi.spyOn(window, 'confirm').mockReturnValueOnce(true)
    expect(confirmDiscard()).toBe(true)
    unmount()
  })

  it('a form that closes no longer counts', () => {
    const { unmount } = renderHook(() => useReportUnsaved(true))
    unmount()
    const ask = vi.spyOn(window, 'confirm')
    expect(confirmDiscard()).toBe(true)
    expect(ask).not.toHaveBeenCalled()
  })
})
