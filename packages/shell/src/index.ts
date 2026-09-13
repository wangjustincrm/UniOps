export type { TabMeta, TabKind, TabSpec, RouteDef } from './tab/types'
export { resolveRoute, deriveTabMeta } from './tab/routeTable'
export { createTabStore } from './tab/tabStore'
export type { TabStoreState, TabStoreOptions } from './tab/tabStore'
export { TabStoreProvider, useTabStore, useTabStoreApi, useOptionalTabStoreApi } from './tab/TabStoreContext'
export { TabHost } from './tab/TabHost'
export { TabBar } from './tab/TabBar'
export { TabRouterSync } from './tab/TabRouterSync'
export { useTabDirty } from './tab/useTabDirty'
export { useReplaceTab } from './tab/useReplaceTab'
export { useBackTab } from './tab/useBackTab'
export { useTabTitle } from './tab/useTabTitle'
export { TabBackLink } from './tab/TabBackLink'
export type { TabBackLinkProps } from './tab/TabBackLink'
export { RouteRenderer } from './tab/RouteRenderer'

// Assistant overlay. Takes an `ask` function from the host app, so this package
// stays free of any app's API client or auth store.
export { Assistant } from './assistant/Assistant'
export type { AssistantProps } from './assistant/Assistant'
export type {
  AssistantCheck, AssistantContext, AssistantKind, AssistantMessage,
  AssistantPreflight, AssistantReply, AssistantSources,
  AssistantWorkflow, AssistantWorkflowEvent, AssistantWorkflowStep,
} from './assistant/types'

// Shared UI primitives (generic; domain wrappers like StatusBadge stay in apps).
export * from './ui'
