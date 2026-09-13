/**
 * URL → assistant context.
 *
 * This is what makes "why can't I submit this?" answerable without the person
 * typing a document number, so the cases that matter are the ones where it must
 * NOT guess: a list page and a /new form have no document, and reporting one
 * would point the assistant at whatever id happened to be in the URL.
 */
import { describe, it, expect } from 'vitest'
import { deriveContext } from './AssistantMount'

const ID = '7c1f2a3b-4d5e-4f60-8a91-b2c3d4e5f607'

describe('deriveContext', () => {
  it('always reports the app and the route', () => {
    expect(deriveContext('/dashboard')).toEqual({ app: 'epms', route: '/dashboard' })
  })

  it('picks up the document being viewed', () => {
    expect(deriveContext(`/pr/${ID}`)).toEqual({
      app: 'epms', route: `/pr/${ID}`, doc_type: 'pr', doc_id: ID,
    })
  })

  it.each(['po', 'gr', 'pa'])('handles /%s/<id>', (kind) => {
    const ctx = deriveContext(`/${kind}/${ID}`)
    expect(ctx.doc_type).toBe(kind)
    expect(ctx.doc_id).toBe(ID)
  })

  it('normalises the plural invoice route', () => {
    // The app routes invoices under /invoices but the backend entity is
    // singular; sending "invoices" would name something the ontology has never
    // heard of.
    expect(deriveContext(`/invoices/${ID}`).doc_type).toBe('invoice')
    expect(deriveContext(`/invoice/${ID}`).doc_type).toBe('invoice')
  })

  it('keeps sub-routes of a document attached to that document', () => {
    expect(deriveContext(`/pr/${ID}/edit`).doc_id).toBe(ID)
  })

  it.each(['/pr', '/pr/new', '/pr/list', '/po/create'])(
    'reports no document for %s',
    (path) => {
      const ctx = deriveContext(path)
      expect(ctx.doc_id).toBeUndefined()
      expect(ctx.doc_type).toBeUndefined()
    }
  )

  it('ignores an id-shaped segment under an unrelated route', () => {
    const ctx = deriveContext(`/admin/users/${ID}`)
    expect(ctx.doc_id).toBeUndefined()
  })

  it('accepts an uppercase uuid', () => {
    expect(deriveContext(`/po/${ID.toUpperCase()}`).doc_id).toBe(ID.toUpperCase())
  })
})
