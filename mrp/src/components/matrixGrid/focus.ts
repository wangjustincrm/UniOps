/**
 * Focus/selection anchors for MatrixGrid.
 *
 * The grid addresses cells by *index* (`rowIdx`/`colIdx`) rather than by id,
 * because keyboard navigation, range selection and the modified-cell
 * highlight all work in grid coordinates. That makes an anchor go stale the
 * moment the underlying `rows`/`cols` arrays shrink beneath it — which the
 * Sales Forecast page does routinely: "Add Product" appends a row and
 * focuses it, and removing that not-yet-saved row takes it straight back
 * out again while the anchor still points one past the end.
 *
 * `pruneFocus` is the single place that decides whether an anchor still
 * addresses a live cell. Keeping it a pure function (rather than an inline
 * bounds check) is what lets `verify.ts` cover the regression.
 */
export interface FocusCell {
  rowIdx: number
  colIdx: number
}

/**
 * Returns `focus` unchanged when it still points at a live cell, otherwise
 * `null`. The **same object** comes back when it is still valid, so this is
 * safe to call from a `setFocus(f => pruneFocus(...))` updater without
 * scheduling a pointless re-render on every rows/cols change.
 */
export function pruneFocus(
  focus: FocusCell | null,
  rowCount: number,
  colCount: number,
): FocusCell | null {
  if (!focus) return null
  if (focus.rowIdx < 0 || focus.rowIdx >= rowCount) return null
  if (focus.colIdx < 0 || focus.colIdx >= colCount) return null
  return focus
}
