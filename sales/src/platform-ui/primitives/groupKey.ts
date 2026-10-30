/** The map key of a group: its values from the outermost level down, as
 *  text (a row's true and a group key's true are the same group). */
export const groupKey = (values: unknown[]): string =>
  JSON.stringify(values.map((v) => (v === undefined || v === null ? null : String(v))))
