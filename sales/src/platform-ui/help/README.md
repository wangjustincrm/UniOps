# `help/` — in-app help, as one self-contained module

Help shown where it is needed (the user, 2026-11-16): a **?** beside what it explains, a badge you tap to read what a status means, and **why a disabled button can't be used**. Contextual help, not tours first (NN/g: tutorials shown up front did not help people finish tasks; help at the moment of need does).

**Self-contained, to take to another project as this folder.** It imports only its own files, `react`, `react-dom`, `@radix-ui/react-popover`, `@radix-ui/react-dialog` and `lucide-react` (Tailwind classes for the look). `importRule.test.ts` fails if a file here imports anything else. The library around it uses it (ActionButton's `disabledReason`); it never uses the library.

## What it gives

| Export | Use |
| --- | --- |
| `HelpProvider` | `source(id) → { title?, body, next? }` for everything under it; `onMissing(id)` is told about an id with no text (warn in development; in production a missing text shows no help) |
| `HelpTip` | by `id` (the provider's text) or `entry`. No children: a small ? icon. Children: they are the trigger (a status badge). Opens on click / tap / Enter, closes on Esc or outside; portalled, at most 20rem and never wider than the screen; `data-help="<id>"` marks it (for a tour, later) |
| `DisabledReason` | why a control can't be used: the control stays enabled but `aria-disabled` (a natively disabled button can't be tapped or focused), so a tap / click / Enter opens the reason and a screen reader hears it as the control's description; no wrapper, so it keeps its width (full width in a phone's ActionBar). While a reason shows, the child's own onClick is dropped and it can't submit a form, whatever control it is. No reason: the control as it is |
| `useHelp(id)` | the text, for a screen that shows it inline |
| `PageHelp` | a page's ? beside its title (PageHeader / ListPage `help`): a side panel (full width on a phone) with what the page is for, its `tasks`, and a search over every text (the provider's `catalog`); no text for the page: no button |
| `searchHelp(all, q)` | the search itself: texts holding every word (title, text, next, tasks), title matches first, case and accents ignored |
| `HelpRegistry`, `HelpId` | typed ids: the module adds its own (`declare module '<path>/help/types' { interface HelpRegistry { 'orders.export': true; [k: \`status:${string}\`]: true } }`) and gets autocomplete and typo errors |

## Layers and the list tools

`HelpProvider layers={[library texts, module texts]}`: a later layer overrides an earlier one by id, an API-backed `source` overrides both, and the search lists them all. The component library's list tools own their texts (`patterns/listHelp.ts`, `PLATFORM_HELP`, ids `list.*`) and mark themselves with `data-help`; `ListPage` adds a "Using lists" section to its page's panel with only the tools that list has (`listTools`). A module writes none of it.

## Tours: one manager, parts declare their place

- `TourManager` (mounted once at the app's root) owns the slot order (`DEFAULT_SLOTS`, overridable by name through `slots`), the one tour on screen (Esc ends it first; a sheet it runs in stays open), and the "?" key (the help of the page in view).
- `TourScope` is a tour's boundary: ListPage, a page shell (Health) and RecordSheet wrap their content in one; the nearest wins, so a record's sheet keeps its parts out of its page's tour.
- A part becomes a stop by declaring itself: `useTourStop({ help, slot, key })` (put `attach` as its ref) or `<TourMark help slot>`, or `HelpTip tourSlot`. `slot` is a name in the slot table (like a z-index, but named), never a number; `key` makes many parts one stop (a list's status badges: the first shown).
- A page's "Show me" walks its scope's shown stops in slot order (`planStops`); a record's "Show me around" (`ShowMeAround`) its own; a search result on the page has Show. No stops: no button. A first visit shows "New here? Show me" (per viewer, in this browser).
- In development the help panel's **Tour inspector** lists the page's stops: order, slot, help id, hidden now, no text.
- Guards (`help.test.tsx`): every slot a component names is in the table; no raw tour numbers anywhere.

## What a project gives it

1. The texts: a `source` function over its own texts (and/or texts from its API), and a `catalog` of them all for the search.
2. Its ids, added to `HelpRegistry`.
3. `help` / `disabledReason` wired into its own components (here: ActionButton, the order status badge).

## Rules

- One text, one place. Texts about business rules live beside the rules (Sales: `sales-api/app/domain/status_help.py`, served at `GET /help/texts`, tested for coverage, length and real button names); texts only a screen needs live in the module's front end (`features/help`).
- Short: a popover is read on a phone (Sales caps a body at 320 characters).
- `data-help` only on one-of-a-kind elements (a header, a field, a button), never on every row.
