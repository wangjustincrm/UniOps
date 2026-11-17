/** The element a step points at: `[data-help="id"]`, or with "prefix*" the first whose id starts so;
 *  only one that is shown (has a box). */
export function findAnchor(anchor: string, root: ParentNode = document): HTMLElement | null {
  const q = (v: string) => v.replace(/["\\]/g, '\\$&')  // an attribute value in quotes (CSS.escape is not everywhere)
  const els = anchor.endsWith('*')
    ? [...root.querySelectorAll<HTMLElement>(`[data-help^="${q(anchor.slice(0, -1))}"]`)]
    : [...root.querySelectorAll<HTMLElement>(`[data-help="${q(anchor)}"]`)]
  return els.find((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 }) ?? null
}


