import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Star } from 'lucide-react'
import { ListPage, PermissionProvider, type ColumnDef } from './index'
import type { ListMeta } from './resource/listMeta'

// One icon per action in every data table (the user, 2026-11-27): Export and New take the platform's icons unless the
// list configures its own.

type Row = { id: number; code: string }
const meta: ListMeta = { version: 1, searchable: false, default_sort: [], fields: [],
  capabilities: { export: { allowed: true, computed: [] }, suggest: false, actions: [] } }
const columns: ColumnDef<Row, unknown>[] = [{ accessorKey: 'code', header: 'Code' }]
const resource = { basePath: '/things', meta: async () => meta, list: async () => ({ items: [{ id: 1, code: 'A1' }], total: 1 }), exportCsv: async () => {} }

function show(props: Partial<Parameters<typeof ListPage<Row>>[0]> = {}) {
  const qc = new QueryClient()
  render(<QueryClientProvider client={qc}><PermissionProvider can={() => true}>
    <ListPage<Row> resource={resource} title="Things" columns={columns}
      create={{ label: 'New thing', permission: 'x', render: () => null }} {...props} />
  </PermissionProvider></QueryClientProvider>)
}
const iconOf = (el: HTMLElement) => el.querySelector('svg')

describe('the default icons', () => {
  it('Export and New take the platform\'s icons', async () => {
    show()
    const exp = await screen.findByRole('button', { name: 'Export CSV' })
    expect(iconOf(exp)).toHaveAttribute('data-icon', 'export')
    expect(iconOf(exp)!.classList.contains('lucide-download')).toBe(true)
    const create = screen.getByRole('button', { name: 'New thing' })
    expect(iconOf(create)).toHaveAttribute('data-icon', 'create')
    expect(iconOf(create)!.classList.contains('lucide-plus')).toBe(true)
  })

  it('a list overrides one by its option (or none), never by hand', async () => {
    show({ exportIcon: <Star data-icon="own" className="h-4 w-4" />, create: { label: 'New thing', permission: 'x', icon: false, render: () => null } })
    expect(iconOf(await screen.findByRole('button', { name: 'Export CSV' }))).toHaveAttribute('data-icon', 'own')
    expect(iconOf(screen.getByRole('button', { name: 'New thing' }))).toBeNull()
  })
})

// Every list takes the defaults: no page places an export or create icon of its own by hand (an override is the list's
// option, ListPage exportIcon / create.icon), and no page draws an Export or New button outside the platform's.
const pages = import.meta.glob<string>(['../features/**/*.tsx', '../app/**/*.tsx', '../pages/**/*.tsx', '!../**/*.test.tsx'],
  { query: '?raw', import: 'default', eager: true })

describe('every list', () => {
  it('no page imports an export or create icon of its own', () => {
    const own = Object.entries(pages).filter(([, src]) => /import\s*\{[^}]*\b(Download|Plus|FileDown|CirclePlus|PlusCircle)\b[^}]*\}\s*from\s*'lucide-react'/.test(src))
    expect(own.map(([f]) => f)).toEqual([])
    expect(Object.keys(pages).length).toBeGreaterThan(50)
  })
})
