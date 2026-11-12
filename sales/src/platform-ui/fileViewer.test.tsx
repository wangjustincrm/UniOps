import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ViewFileButton, type FileData } from './index'
import { fileKind, formatText } from './lib/files'

const bytes = (s: string) => new TextEncoder().encode(s)
const file = (body: BlobPart, name: string, type = 'application/octet-stream'): FileData => ({ blob: new Blob([body], { type }), name, type })

const ISA = 'ISA*00*          *00*          *ZZ*CRM            *ZZ*WHSE           *261112*1200*U*00401*000000001*0*P*>~'
const X12 = `${ISA}GS*OW*CRM*WHSE*20261112*1200*1*X*004010~ST*856*0001~SE*2*0001~`

describe('file helpers', () => {
  it('tells a PDF, a zip, an EDI file and JSON apart by type, name or first bytes', () => {
    expect(fileKind(file('', 'x.bin', 'application/pdf'), bytes(''))).toBe('pdf')
    expect(fileKind(file('', 'stored-1'), bytes('%PDF-1.4'))).toBe('pdf')
    expect(fileKind(file('', 'Order.zip', 'application/zip'), bytes('PK'))).toBe('other')
    expect(fileKind(file('', 'out-1'), bytes(X12))).toBe('text')
    expect(fileKind(file('', 'stored-2'), new Uint8Array([0x89, 0x00, 0x01]))).toBe('other')
  })

  it('lays an X12 file out one segment per line and indents JSON; plain text stays as it is', () => {
    expect(formatText(X12)?.split('\n')).toEqual([ISA, 'GS*OW*CRM*WHSE*20261112*1200*1*X*004010~', 'ST*856*0001~', 'SE*2*0001~'])
    expect(formatText('{"Invoice":[{"Number":"IN-1"}]}')).toBe('{\n  "Invoice": [\n    {\n      "Number": "IN-1"\n    }\n  ]\n}')
    expect(formatText('just a note')).toBeNull()
  })
})

describe('ViewFileButton', () => {
  const created: Blob[] = []
  beforeEach(() => {
    created.length = 0
    URL.createObjectURL = vi.fn((b: Blob) => { created.push(b); return `blob:${created.length}` })
    URL.revokeObjectURL = vi.fn()
  })
  afterEach(() => vi.restoreAllMocks())

  it('shows an EDI file laid out, "as the file is" on request, and downloads it only when asked', async () => {
    const load = vi.fn(async () => file(X12, 'out-1.edi'))
    render(<ViewFileButton source={{ key: 'd1', title: 'EDI (sent)', load }}>View</ViewFileButton>)
    expect(load).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'View' }))
    const pre = await screen.findByLabelText('EDI (sent) contents')
    expect(pre.textContent?.split('\n')).toHaveLength(4)
    fireEvent.click(screen.getByRole('button', { name: 'As the file is' }))
    expect(screen.getByLabelText('EDI (sent) contents').textContent).toBe(X12)
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    expect(click).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Download' }))
    expect(click).toHaveBeenCalledTimes(1)
    expect(load).toHaveBeenCalledTimes(1)  // the bytes already read are saved: not fetched again
  })

  it('shows a PDF in a frame, with Print and Open in a new tab', async () => {
    render(<ViewFileButton source={{ key: 'p1', title: 'Invoice IN-1', load: async () => file('%PDF-1.4 x', 'IN-1.pdf', 'application/pdf') }}>Invoice PDF</ViewFileButton>)
    fireEvent.click(screen.getByRole('button', { name: 'Invoice PDF' }))
    expect(await screen.findByTitle('Invoice IN-1')).toHaveAttribute('src', 'blob:1')
    expect(screen.getByRole('button', { name: 'Print' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open in a new tab' })).toBeInTheDocument()
  })

  it('says a zip can only be downloaded, and a failure in words', async () => {
    const { unmount } = render(<ViewFileButton source={{ key: 'z', title: 'Packet', load: async () => file('PK..', 'Order.zip', 'application/zip') }}>Zip</ViewFileButton>)
    fireEvent.click(screen.getByRole('button', { name: 'Zip' }))
    expect(await screen.findByText(/Order.zip cannot be shown here/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Print' })).toBeNull()
    unmount()
    render(<ViewFileButton source={{ key: 'e', title: 'Gone', load: async () => { throw new Error('Not found') } }}>Broken</ViewFileButton>)
    fireEvent.click(screen.getByRole('button', { name: 'Broken' }))
    await waitFor(() => expect(screen.getByText(/Could not open the file: Not found/)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Download' })).toBeDisabled()
  })
})
