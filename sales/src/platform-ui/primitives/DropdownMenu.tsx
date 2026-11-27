import * as RM from '@radix-ui/react-dropdown-menu'
import { useRef, type ReactNode } from 'react'
import { cn } from '../lib/cn'

export interface MenuItem {
  label: string
  icon?: ReactNode
  /** A promise is fine: whoever renders the menu may run it as an action (RecordSheet does). */
  onSelect: () => void | Promise<unknown>
  disabled?: boolean
  /** Why it is disabled: a second line under its label. */
  disabledReason?: string
  /** A second line under its label (what it does). */
  hint?: string
  /** Red: it destroys or cancels something. */
  tone?: 'destructive'
  /** A line above this item. */
  separated?: boolean
  /** The item opens a dialog or sheet: the menu does not take the focus back
   *  when it closes (it belongs to the dialog now). */
  opensDialog?: boolean
}

export interface DropdownMenuProps {
  trigger: ReactNode
  items: MenuItem[]
  /** The menu's accessible name (e.g. "Code column"). */
  label: string
  align?: 'start' | 'center' | 'end'
  /** Opened from outside (a right-click menu); else by its trigger. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

/** A menu of actions on a trigger (Radix): keyboard, focus and outside click handled. */
export function DropdownMenu({ trigger, items, label, align = 'start', open, onOpenChange }: DropdownMenuProps) {
  const handOff = useRef(false)
  return (
    // Not modal: a column menu needs no focus trap or scroll lock.
    <RM.Root modal={false} open={open} onOpenChange={onOpenChange}>
      <RM.Trigger asChild>{trigger}</RM.Trigger>
      <RM.Portal>
        <RM.Content
          aria-label={label}
          align={align}
          sideOffset={4}
          onCloseAutoFocus={(e) => {
            if (handOff.current) e.preventDefault()
            handOff.current = false
          }}
          className="z-50 min-w-[11rem] rounded-lg border border-neutral-200 bg-white p-1 text-sm normal-case tracking-normal text-neutral-700 shadow-lg focus:outline-none"
        >
          {items.map((it) => (
            <div key={it.label}>
              {it.separated && <RM.Separator className="my-1 h-px bg-neutral-100" />}
              <RM.Item
                disabled={it.disabled}
                onSelect={() => {
                  handOff.current = !!it.opensDialog
                  it.onSelect()
                }}
                className={cn('flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 outline-none',
                  'data-[highlighted]:bg-neutral-100 data-[disabled]:cursor-default data-[disabled]:opacity-40')}
              >
                {it.icon}
                {(it.disabled && it.disabledReason) || it.hint ? (
                  <span className="flex flex-col">
                    <span className={cn(it.tone === 'destructive' && 'text-danger-700')}>{it.label}</span>
                    <span className="text-xs text-neutral-500">{it.disabled && it.disabledReason ? it.disabledReason : it.hint}</span>
                  </span>
                ) : <span className={cn(it.tone === 'destructive' && 'text-danger-700')}>{it.label}</span>}
              </RM.Item>
            </div>
          ))}
        </RM.Content>
      </RM.Portal>
    </RM.Root>
  )
}
