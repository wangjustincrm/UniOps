// The help module: the only way in. Self-contained (see README.md): it imports nothing from the
// library around it, so it can be taken to another project as this one folder.
export { HelpProvider } from './HelpProvider'
export { useHelp } from './useHelp'
export { HelpTip } from './HelpTip'
export { DisabledReason } from './DisabledReason'
export { PageHelp } from './HelpPanel'
export { searchHelp, type HelpHit } from './search'
export type { HelpCatalog, HelpEntry, HelpId, HelpRegistry, HelpSource } from './types'
