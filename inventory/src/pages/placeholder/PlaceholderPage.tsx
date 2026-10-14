// Stand-in for the pages that land in later phases (design §13). Each one
// says which phase brings it, so nobody mistakes an empty page for missing
// data.
import { Card, CardContent } from '@uniops/shell'

export default function PlaceholderPage({ title, message }: { title: string; message: string }) {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-neutral-900">{title}</h1>
      <Card>
        <CardContent className="text-sm text-neutral-500">{message}</CardContent>
      </Card>
    </div>
  )
}
