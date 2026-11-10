import { useCallback } from 'react'
import { useGoTo, useOpenView } from '@/platform-ui'
import type { ListLink } from './healthApi'

/** Opens the list behind a card or check: its own filters, under the card's name, in the page's
 *  (possibly already open) tab; a link without filters just goes to its page. */
export function useOpenList() {
  const openView = useOpenView()
  const goTo = useGoTo()
  return useCallback((link: ListLink, name: string) => {
    if (link.resource && link.filters?.length) openView(link.page, link.resource, { name, state: { filters: link.filters } })
    else goTo(link.page)
  }, [openView, goTo])
}
