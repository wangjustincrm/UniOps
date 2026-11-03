import { Tooltip } from '@/platform-ui'

/** A partner as people know it: its name, the code in a tooltip (the detail
 *  shows both); the code alone when the name is not known. */
export function PartnerCell({ name, code }: { name: string | null | undefined; code: string | null | undefined }) {
  if (!name) return <>{code ?? '—'}</>
  return code ? <Tooltip tip={`Partner code ${code}`}>{name}</Tooltip> : <>{name}</>
}
