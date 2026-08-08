// Split out of BomTreeNode.tsx so that file only exports components, per
// the repo's react-refresh/only-export-components lint rule (same reason
// hooks/useToasts.ts was split from components/Toast.tsx).
import type { ExplodeNode } from './bomApi'

export interface FlaggedNode {
  path: string
  material_code: string
  reason: 'missing_bom' | 'cycle_detected' | 'node_limit_reached'
}

/** Depth-first walk collecting every flagged node — feeds the page-level
 *  summary banner (mockup's bottom "! S0074-R: ... — cannot explode" line)
 *  so a planner sees every gap in one place, not just buried in the tree. */
export function collectFlags(node: ExplodeNode, path = '0'): FlaggedNode[] {
  const out: FlaggedNode[] = []
  if (node.missing_bom) out.push({ path, material_code: node.material_code, reason: 'missing_bom' })
  if (node.cycle_detected) out.push({ path, material_code: node.material_code, reason: 'cycle_detected' })
  if (node.node_limit_reached) out.push({ path, material_code: node.material_code, reason: 'node_limit_reached' })
  node.children.forEach((child, i) => out.push(...collectFlags(child, `${path}.${i}`)))
  return out
}
