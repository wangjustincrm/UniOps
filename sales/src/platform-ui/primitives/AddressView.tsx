/** An address shown as its lines (name, attention, street, city / province /
 *  postal code, country, phone) and an optional note; a dash when empty. Visual only. */
export function AddressView({ value, note }: { value: Record<string, unknown> | null | undefined; note?: string }) {
  const a = value ?? {}
  const s = (k: string) => (typeof a[k] === 'string' && (a[k] as string).trim() ? (a[k] as string).trim() : null)
  const lines = [
    s('name') ?? s('company'), s('attention') && `Attn: ${s('attention')}`, s('address1'), s('address2'),
    [s('city'), s('province') ?? s('province_code'), s('zip')].filter(Boolean).join(' ') || null,
    s('country') ?? s('country_code'), s('phone'),
  ].filter(Boolean) as string[]
  return (
    <span className="flex flex-col">
      {lines.length === 0 ? <span className="text-neutral-400">—</span> : lines.map((l, i) => <span key={i}>{l}</span>)}
      {note && <span className="text-xs text-neutral-500">{note}</span>}
    </span>
  )
}
