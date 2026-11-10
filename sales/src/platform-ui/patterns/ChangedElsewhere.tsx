import { Callout } from '../primitives/Callout'
import { Button } from '../primitives/shell'

/** Said in a form whose record someone else saved while this one has unsaved input (useEditVersion). */
export function ChangedElsewhere({ onReload }: { onReload: () => void }) {
  return (
    <Callout tone="warning" title="Someone else saved this record meanwhile" label="Changed elsewhere">
      <span>Your changes are still here, but saving them is refused until you take the new version.</span>
      <span><Button type="button" size="sm" variant="secondary" onClick={onReload}>Reload (discards yours)</Button></span>
    </Callout>
  )
}
