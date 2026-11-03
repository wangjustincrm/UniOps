/** A partner as people know it: its name, with the code beside it
 *  ("Loblaws (AC-9898)"); the code alone when the name is not known. */
export function PartnerCell({ name, code }: { name: string | null | undefined; code: string | null | undefined }) {
  if (!name) return <>{code ?? '—'}</>
  return <span>{name}{code && <span className="text-neutral-500"> ({code})</span>}</span>
}
