/**
 * The plant tree.
 *
 * Read-only here: `locations` is master data owned by the master-data service,
 * and Safety is one of several things that will read it. The screen lives in
 * this module because HSE is who keeps it current.
 */
import { useQuery } from '@tanstack/react-query'
import { QrCode } from 'lucide-react'
import { api } from '@/lib/api'

interface Node {
  id: string; code: string; name: string; level: string; path: string
  depth: number; access_area: string | null; qr_token: string | null
  is_active: boolean; children: Node[]
}

const LEVEL_LABEL: Record<string, string> = {
  site: 'Site', building: 'Building', department: 'Department',
  line: 'Line', sub_line: 'Line', room: 'Room', workstation: 'Workstation',
}

export default function LocationsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['locations'],
    queryFn: () => api.get<Node[]>('/api/v1/settings/locations'),
  })

  if (isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>

  return (
    <div className="p-4">
      <h1 className="text-lg font-bold tracking-tight text-neutral-900">Plant areas</h1>
      <p className="mb-3 text-xs text-neutral-500">
        Every incident, inspection and corrective action is filed against one of
        these. Maintained centrally with the rest of the master data.
      </p>
      <ul className="rounded-xl border border-neutral-200 bg-white">
        {data?.map((n) => <TreeNode key={n.id} node={n} />)}
      </ul>
    </div>
  )
}

function TreeNode({ node }: { node: Node }) {
  return (
    <>
      <li
        className="flex items-center gap-2.5 border-b border-neutral-100 px-3.5 py-2.5 last:border-b-0"
        style={{ paddingLeft: `${14 + node.depth * 18}px` }}
      >
        <span className="min-w-0 flex-1 text-sm text-neutral-800">{node.name}</span>
        <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-500">
          {LEVEL_LABEL[node.level] ?? node.level}
        </span>
        {node.access_area && (
          <span className="hidden text-xs text-neutral-400 sm:inline">{node.access_area}</span>
        )}
        {node.qr_token && (
          <QrCode className="h-3.5 w-3.5 text-neutral-300" aria-label="Has a scannable code" />
        )}
      </li>
      {node.children?.map((c) => <TreeNode key={c.id} node={c} />)}
    </>
  )
}
