/**
 * A JHSC recommendation's reply, as written.
 *
 * ★ UAT 13, TC-08: a reply made as a corrective action is stored as its title,
 * a blank line, then its detail; it read as one run-on line. Shown as "What
 * will be done" and "Detail", each on its own lines. ★ Review R4: an action
 * with no detail is still headed "What will be done". R3: the same on the
 * JHSC list, which had its own copy that ran the lines together.
 */
export default function JhscReplyText({ text, fromAction }: { text: string; fromAction: boolean }) {
  if (!fromAction) return <p className="whitespace-pre-wrap">{text}</p>
  const cut = text.indexOf('\n\n')
  const title = cut === -1 ? text : text.slice(0, cut)
  const detail = cut === -1 ? '' : text.slice(cut + 2)
  return (
    <>
      <p className="text-[11px] font-semibold uppercase tracking-wide text-neutral-500">What will be done</p>
      <p className="whitespace-pre-wrap font-medium text-neutral-800">{title}</p>
      {detail && (
        <>
          <p className="mt-1.5 text-[11px] font-semibold uppercase tracking-wide text-neutral-500">Detail</p>
          <p className="whitespace-pre-wrap">{detail}</p>
        </>
      )}
    </>
  )
}
