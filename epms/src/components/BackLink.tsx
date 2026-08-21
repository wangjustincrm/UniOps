import { TabBackLink, useBackTab, useTabTitle, type TabBackLinkProps } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'

/**
 * "Back to the parent" link, bound to this app's route table.
 *
 * Unlike a plain <Link>, clicking it CLOSES the tab it lives in: the parent tab
 * is focused if it is already open, otherwise the current tab becomes it. Use it
 * for every `← Back to X` affordance so tabs don't pile up as users drill in and
 * out of documents.
 */
export function BackLink(props: Omit<TabBackLinkProps, 'routes'>) {
  return <TabBackLink routes={epmsRoutes} {...props} />
}

/** Imperative form of {@link BackLink}, for buttons and post-action redirects. */
export function useBack() {
  return useBackTab(epmsRoutes)
}

/**
 * Rename the current tab once the document number is known, so tabs read
 * `PO-20260815-0007` instead of `PO a5f40064`. Pass `undefined` while loading.
 */
export function useDocTabTitle(title: string | undefined | null) {
  useTabTitle(epmsRoutes, title)
}
