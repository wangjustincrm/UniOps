# `help/` — in-app help, as one self-contained module

Help shown where it is needed (the user, 2026-11-16): a **?** beside what it explains, a badge you tap to read what a status means, and **why a disabled button can't be used**. Contextual help, not tours first (NN/g: tutorials shown up front did not help people finish tasks; help at the moment of need does).

**Self-contained, to take to another project as this folder.** It imports only its own files, `react`, `@radix-ui/react-popover`, `@radix-ui/react-dialog` and `lucide-react` (Tailwind classes for the look). `importRule.test.ts` fails if a file here imports anything else. The library around it uses it (ActionButton's `disabledReason`); it never uses the library.

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

## What a project gives it

1. The texts: a `source` function over its own texts (and/or texts from its API), and a `catalog` of them all for the search.
2. Its ids, added to `HelpRegistry`.
3. `help` / `disabledReason` wired into its own components (here: ActionButton, the order status badge).

## Rules

- One text, one place. Texts about business rules live beside the rules (Sales: `sales-api/app/domain/status_help.py`, served at `GET /help/texts`, tested for coverage, length and real button names); texts only a screen needs live in the module's front end (`features/help`).
- Short: a popover is read on a phone (Sales caps a body at 320 characters).
- `data-help` only on one-of-a-kind elements (a header, a field, a button), never on every row.
