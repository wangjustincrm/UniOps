import { describe, expect, it } from 'vitest'
import { ATTACHMENT_RULE, INVOICE_FILE_RULE, partitionUploads, uploadProblem } from './uploadLimits'

const MB = 1024 * 1024

describe('upload limits (ISS-0093)', () => {
  it('an invoice upload refuses a .zip and accepts the INV-001 types', () => {
    expect(uploadProblem({ name: 'R2-ZIP-TEST-01.zip', size: 10 }, INVOICE_FILE_RULE)).toContain('PDF, JPG, JPEG, PNG')
    for (const n of ['a.pdf', 'b.JPG', 'c.jpeg', 'd.png']) {
      expect(uploadProblem({ name: n, size: 10 }, INVOICE_FILE_RULE)).toBeNull()
    }
    expect(uploadProblem({ name: 'sheet.xlsx', size: 10 }, INVOICE_FILE_RULE)).not.toBeNull()
  })

  it('an invoice over 25 MB is refused', () => {
    expect(uploadProblem({ name: 'a.pdf', size: 25 * MB }, INVOICE_FILE_RULE)).toBeNull()
    expect(uploadProblem({ name: 'a.pdf', size: 25 * MB + 1 }, INVOICE_FILE_RULE)).toContain('25 MB')
  })

  it('attachments take the File Server types, including saved emails, and not archives', () => {
    const { ok, problems } = partitionUploads(
      [{ name: 'quote.msg', size: 1 }, { name: 'pack.XLSX', size: 1 }, { name: 'all.zip', size: 1 },
       { name: 'run.exe', size: 1 }, { name: 'noext', size: 1 }],
      ATTACHMENT_RULE)
    expect(ok.map((f) => f.name)).toEqual(['quote.msg', 'pack.XLSX'])
    expect(problems).toHaveLength(3)
  })

  it('every drop zone line states the size the server enforces', () => {
    expect(INVOICE_FILE_RULE.hint).toBe('PDF, JPG, JPEG, PNG · Max 25 MB per file')
    expect(ATTACHMENT_RULE.hint).toContain('Max 25 MB per file')
    expect(INVOICE_FILE_RULE.accept).toBe('.pdf,.jpg,.jpeg,.png')
  })
})
