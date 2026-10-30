import { useState } from 'react'
import { Check, ChevronDown, MoreHorizontal, Star } from 'lucide-react'
import { Dialog } from '../primitives/Dialog'
import { DropdownMenu } from '../primitives/DropdownMenu'
import { Popover } from '../primitives/Popover'
import { Button, Input } from '../primitives/shell'
import { cn } from '../lib/cn'
import { errorText } from '../resource/businessError'
import { ConfirmAction } from './ConfirmAction'
import type { CurrentView, ViewPickerModel } from './views'

const ERRORS = { 'view.name_taken': 'You already have a view with that name.' }

/** The view picker left of a list's toolbar (design §13.19): system views and
 *  my views; a dot when the list differs from the chosen view, then Save /
 *  Save as new / Reset; per view of mine: Rename, Set as default, Delete. */
export function ViewPicker({ model }: { model: ViewPickerModel }) {
  const [open, setOpen] = useState(false)
  const [naming, setNaming] = useState<{ title: string; initial: string; done: (name: string) => Promise<void> } | null>(null)
  const [deleting, setDeleting] = useState<{ id: number; name: string } | null>(null)
  const { current, dirty } = model
  const isCurrent = (v: CurrentView) => current?.kind === v.kind &&
    (v.kind === 'system' ? current.kind === 'system' && current.key === v.key : current.kind === 'mine' && current.id === v.id)
  const choose = (v: CurrentView) => { model.select(v); setOpen(false) }
  const saveAs = () => setNaming({ title: 'Save as a new view', initial: '', done: model.saveAs })

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Popover
        open={open}
        onOpenChange={setOpen}
        className="w-72 p-1"
        trigger={
          <button type="button" aria-label={`View: ${current?.name ?? 'none'}${dirty ? ' (unsaved changes)' : ''}`}
            className="inline-flex h-10 max-w-[16rem] items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 text-sm font-medium text-neutral-800 hover:bg-neutral-50">
            <span className="truncate">{current?.name ?? 'Views'}</span>
            {dirty && <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-warning-500" />}
            <ChevronDown className="h-4 w-4 shrink-0 text-neutral-400" />
          </button>
        }
      >
        <Section title="System views">
          {model.system.map((s) => {
            const v: CurrentView = { kind: 'system', key: s.key, name: s.name }
            return <Row key={s.key} label={s.name} current={isCurrent(v)} onChoose={() => choose(v)} />
          })}
        </Section>
        <Section title="My views">
          {model.mine.length === 0 && <p className="px-2 py-1.5 text-xs text-neutral-400">None yet: change the list, then Save as new.</p>}
          {model.mine.map((m) => {
            const v: CurrentView = { kind: 'mine', id: m.id, name: m.name }
            return (
              <Row key={m.id} label={m.name} current={isCurrent(v)} isDefault={m.is_default} onChoose={() => choose(v)}
                actions={
                  <DropdownMenu
                    label={`${m.name} view`}
                    align="end"
                    items={[
                      { label: 'Rename', opensDialog: true,
                        onSelect: () => { setOpen(false); setNaming({ title: 'Rename the view', initial: m.name, done: (n) => model.rename(m.id, n) }) } },
                      { label: m.is_default ? 'Remove as default' : 'Set as default', onSelect: () => { void model.setDefault(m.id, !m.is_default) } },
                      { label: 'Delete', opensDialog: true, separated: true, onSelect: () => { setOpen(false); setDeleting({ id: m.id, name: m.name }) } },
                    ]}
                    trigger={
                      <button type="button" aria-label={`${m.name}: more`} className="rounded p-1 text-neutral-400 hover:bg-neutral-200 hover:text-neutral-700">
                        <MoreHorizontal className="h-3.5 w-3.5" />
                      </button>
                    }
                  />
                }
              />
            )
          })}
        </Section>
      </Popover>
      {dirty && (
        <>
          <span className="text-xs text-warning-700">Unsaved changes</span>
          <Button size="sm" variant="secondary" disabled={model.busy}
            onClick={() => (current?.kind === 'mine' ? void model.save() : saveAs())}>Save</Button>
          <Button size="sm" variant="secondary" disabled={model.busy} onClick={saveAs}>Save as new</Button>
          <Button size="sm" variant="ghost" disabled={model.busy} onClick={model.reset}>Reset</Button>
        </>
      )}
      {model.stale && !dirty && (
        <span role="status" className="text-xs text-warning-700">Saved by an older version: review it, then Save.</span>
      )}
      {model.error != null && !naming && <span role="alert" className="text-xs text-danger-600">{errorText(model.error, ERRORS)}</span>}
      {naming && <NameDialog {...naming} onClose={() => setNaming(null)} />}
      <ConfirmAction
        open={deleting !== null}
        onOpenChange={(o) => { if (!o) setDeleting(null) }}
        title={`Delete the view "${deleting?.name ?? ''}"?`}
        description="Only the view is deleted; nothing in the list changes."
        confirmLabel="Delete"
        destructive
        onConfirm={async () => { if (deleting) await model.remove(deleting.id) }}
      />
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div role="group" aria-label={title} className="py-1">
      <p className="px-2 pb-1 text-xs font-medium uppercase tracking-wide text-neutral-400">{title}</p>
      <ul className="flex flex-col">{children}</ul>
    </div>
  )
}

function Row({ label, current, isDefault, onChoose, actions }: {
  label: string; current: boolean; isDefault?: boolean; onChoose: () => void; actions?: React.ReactNode
}) {
  return (
    <li className={cn('flex items-center gap-1 rounded', current && 'bg-neutral-100')}>
      <button type="button" aria-current={current || undefined} onClick={onChoose}
        className="flex min-w-0 flex-1 items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-neutral-50">
        <Check className={cn('h-3.5 w-3.5 shrink-0', current ? 'opacity-100' : 'opacity-0')} />
        <span className="truncate">{label}</span>
        {isDefault && <Star aria-label="default" className="h-3 w-3 shrink-0 fill-current text-warning-500" />}
      </button>
      {actions}
    </li>
  )
}

function NameDialog({ title, initial, done, onClose }: {
  title: string; initial: string; done: (name: string) => Promise<void>; onClose: () => void
}) {
  const [name, setName] = useState(initial)
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  const submit = async () => {
    if (!name.trim() || pending) return
    setPending(true)
    setError(null)
    try {
      await done(name.trim())
      onClose()
    } catch (e) {
      setError(e)
    } finally {
      setPending(false)
    }
  }
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose() }} title={title}
      footer={<>
        <Button variant="secondary" onClick={onClose} disabled={pending}>Cancel</Button>
        <Button onClick={() => void submit()} disabled={pending || !name.trim()}>Save</Button>
      </>}>
      <form onSubmit={(e) => { e.preventDefault(); void submit() }} className="flex flex-col gap-2">
        <Input aria-label="View name" value={name} autoFocus maxLength={80} onChange={(e) => setName(e.target.value)} />
        {error != null && <p role="alert" className="text-sm text-danger-600">{errorText(error, ERRORS)}</p>}
      </form>
    </Dialog>
  )
}
