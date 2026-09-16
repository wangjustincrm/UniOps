/**
 * The plant tree.
 *
 * Read-only here: `locations` is master data owned by the master-data service,
 * and Safety is one of several things that will read it. The screen lives in
 * this module because HSE is who keeps it current.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { QrCode } from 'lucide-react'
import { api } from '@/lib/api'
import QrPanel from '@/components/QrPanel'

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
  // The sticker by a door is the one thing here somebody uses without being
  // asked to: scanning it opens the incident form with the area filled in.
  const [showing, setShowing] = useState<Node | null>(null)
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
      {showing && (
        <div className="mb-3 rounded-xl border border-neutral-200 bg-white p-3.5">
          <QrPanel token={showing.qr_token} caption={showing.name} sub={showing.path} />
          <button type="button" onClick={() => setShowing(null)}
                  className="mt-2 text-xs text-neutral-500 hover:text-neutral-800">
            Close
          </button>
        </div>
      )}
      <ul className="rounded-xl border border-neutral-200 bg-white">
        {data?.map((n) => <TreeNode key={n.id} node={n} onShowCode={setShowing} />)}
      </ul>
    </div>
  )
}

function TreeNode({ node, onShowCode }: { node: Node; onShowCode: (n: Node) => void }) {
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
          <button
            type="button"
            onClick={() => onShowCode(node)}
            title="Show the code to print for this area"
            className="flex min-h-[32px] items-center gap-1 rounded-md px-1.5 text-xs text-neutral-400 hover:bg-neutral-50 hover:text-neutral-800"
          >
            <QrCode className="h-3.5 w-3.5" aria-hidden />
            Code
          </button>
        )}
      </li>
      {node.children?.map((c) => <TreeNode key={c.id} node={c} onShowCode={onShowCode} />)}
    </>
  )
}
