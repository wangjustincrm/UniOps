%%#platform%%
import { EmptyState } from '@/platform-ui'

export default function HomePage() {
  return <EmptyState title="%%label%%" description="The module skeleton is running." />
}
%%/platform%%
%%^platform%%
export default function HomePage() {
  return (
    <div className="rounded-lg border border-neutral-200 bg-white p-8 text-center text-sm text-neutral-500">
      <p className="font-medium text-neutral-800">%%label%%</p>
      <p>The module skeleton is running.</p>
    </div>
  )
}
%%/platform%%
