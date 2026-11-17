// The help module's types (see README.md). Self-contained: nothing here knows the module that uses it.

/** The help ids a module has, added by that module (TypeScript module augmentation):
 *    declare module '@/platform-ui/help' { interface HelpRegistry { 'orders.export': true; [k: `status:${string}`]: true } }
 *  Until a module adds any, an id is any string. */
// eslint-disable-next-line @typescript-eslint/no-empty-object-type
export interface HelpRegistry {}
export type HelpId = keyof HelpRegistry extends never ? string : Extract<keyof HelpRegistry, string>

/** One help text: what it is (`body`), optionally a heading and what to do next. */
export interface HelpEntry {
  title?: string
  body: string
  next?: string
  /** A page's common tasks (its help panel lists them). */
  tasks?: string[]
}

/** Every text the module has, for the help panel's search: its id and text. */
export type HelpCatalog = () => { id: string; entry: HelpEntry }[]

/** Where the texts come from: the module looks an id up (its own texts, texts from its API…). */
export type HelpSource = (id: string) => HelpEntry | undefined
