/**
 * One-off verification script for the pure paste-parsing and undo-stack
 * logic used by MatrixGrid.
 *
 * mrp has no test framework yet, and neither do its sibling apps
 * (epms/portal/oa) — only the shared @uniops/shell *package* has vitest,
 * for its own tab-router/store logic. Per task-9-brief.md's fallback
 * clause, this script exercises the pure functions with plain asserts
 * instead of inventing a new per-app test runner. See task-9-report.md
 * for the full verification record (this script + manual browser checks).
 *
 * Run with:  npx tsx src/components/matrixGrid/verify.ts   (from mrp/)
 */
import {
  parseClipboardText, isMultiCellPaste, parseNumericCell, planPaste,
  applyPasteUpdates, formatPasteReport, selectionToTsv, cellKey,
  type GridRow, type GridCol,
} from './pasteLogic'
import {
  initHistory, commitCells, undo, redo, currentCells, canUndo, canRedo,
} from './history'

let failures = 0
function check(name: string, cond: boolean) {
  if (cond) {
    console.log(`  ok  ${name}`)
  } else {
    failures++
    console.log(`FAIL  ${name}`)
  }
}
function eq(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

// ── parseClipboardText ──────────────────────────────────────────────────
console.log('parseClipboardText')
check('splits rows on \\n and cols on \\t', eq(
  parseClipboardText('a\tb\nc\td'),
  [['a', 'b'], ['c', 'd']],
))
check('strips \\r', eq(
  parseClipboardText('a\tb\r\nc\td\r\n'),
  [['a', 'b'], ['c', 'd']],
))
check('drops trailing blank line', eq(
  parseClipboardText('a\tb\n'),
  [['a', 'b']],
))
check('empty string -> []', eq(parseClipboardText(''), []))

// ── isMultiCellPaste ─────────────────────────────────────────────────────
console.log('isMultiCellPaste')
check('single value is not multi-cell', isMultiCellPaste('20000') === false)
check('tab-separated is multi-cell', isMultiCellPaste('20000\t18000') === true)
check('newline-separated is multi-cell', isMultiCellPaste('20000\n18000') === true)

// ── parseNumericCell ─────────────────────────────────────────────────────
console.log('parseNumericCell')
check('"20,000" -> 20000', eq(parseNumericCell('20,000'), { kind: 'value', value: 20000 }))
check('"$1,234.50" -> 1234.5', eq(parseNumericCell('$1,234.50'), { kind: 'value', value: 1234.5 }))
check('blank -> blank (not zero)', eq(parseNumericCell('   '), { kind: 'blank' }))
check('"abc" -> invalid', eq(parseNumericCell('abc'), { kind: 'invalid' }))
// parseFloat's leading-numeric-prefix behaviour is intentional here — it's
// the exact same `parseFloat` call the blueprint uses (spec: "parseFloat +
// Number.isFinite"), so "12abc" parses as 12, matching Excel-paste leniency
// for stray trailing characters. Only a cell with NO leading numeric prefix
// (e.g. "abc") is flagged invalid.
check('"12abc" -> value 12 (parseFloat leading-prefix behaviour, matches blueprint)', eq(parseNumericCell('12abc'), { kind: 'value', value: 12 }))
// Deliberate deviation from the epms budget blueprint (BreakdownMatrixModal.tsx
// rejects `value < 0`): this module accepts negatives — see the file header
// comment for why. Pinned here so a future "helpfully" re-added `value < 0`
// rejection shows up as a failing check, not a silent behaviour change.
check('"-500" -> value -500 (negatives accepted, unlike the budget blueprint)', eq(parseNumericCell('-500'), { kind: 'value', value: -500 }))

// ── planPaste: scenario 1 — 3x4 block lands correctly, "20,000" -> 20000 ──
console.log('planPaste: 3x4 block at anchor')
const rows: GridRow[] = Array.from({ length: 6 }, (_, i) => ({ id: `p${i}`, label: `Product ${i}` }))
const cols: GridCol[] = Array.from({ length: 6 }, (_, i) => ({ id: `m${i}`, label: `Month ${i}` }))
const block3x4 = ['20,000\t20,000\t20,000\t18,000', '12,000\t12,000\t12,000\t12,000', '5,000\t5,000\t5,000\t5,000'].join('\n')
{
  const plan = planPaste(block3x4, { rowIdx: 1, colIdx: 1 }, rows, cols, new Set())
  check('3 rows x 4 cols = 12 updates', plan.updates.length === 12)
  check('affectedRows=3, affectedCols=4, totalCells=12', plan.affectedRows === 3 && plan.affectedCols === 4 && plan.totalCells === 12)
  check('not clipped', !plan.clippedRows && !plan.clippedCols)
  const first = plan.updates.find((u) => u.key === cellKey('p1', 'm1'))
  check('anchor cell (p1,m1) = 20000 (comma parsed)', first?.value === 20000)
  const lastRowFirstCol = plan.updates.find((u) => u.key === cellKey('p3', 'm1'))
  check('row 3 (p3,m1) = 5000', lastRowFirstCol?.value === 5000)
}

// ── planPaste: scenario 2 — clipped at grid edge, not errored ────────────
console.log('planPaste: clipped at edge')
{
  // 6x6 grid, anchor at (4,4) with a 3x3 block -> only 2 rows / 2 cols fit
  const block3x3 = ['1\t2\t3', '4\t5\t6', '7\t8\t9'].join('\n')
  const plan = planPaste(block3x3, { rowIdx: 4, colIdx: 4 }, rows, cols, new Set())
  check('clipped rows and cols flagged', plan.clippedRows && plan.clippedCols)
  check('only 2x2=4 updates land (no throw)', plan.updates.length === 4)
  check('affectedRows=2, affectedCols=2', plan.affectedRows === 2 && plan.affectedCols === 2)
}

// ── planPaste: scenario 3 — frozen cells skipped + counted, invalid flagged, blank skipped ──
console.log('planPaste: frozen + invalid + blank')
{
  const frozen = new Set([cellKey('p0', 'm1'), cellKey('p0', 'm2')])
  const block = ['10\t20\t30', '\tabc\t40'].join('\n') // row0: all numeric but 2 frozen; row1: blank, invalid, valid
  const plan = planPaste(block, { rowIdx: 0, colIdx: 0 }, rows, cols, frozen)
  check('2 frozen cells skipped', plan.skippedFrozen === 2)
  check('1 invalid cell reported (abc)', plan.invalidCells.length === 1 && plan.invalidCells[0].raw === 'abc')
  check('blank cell produced no update and no invalid entry', plan.updates.every((u) => u.key !== cellKey('p1', 'm0')))
  check('valid non-frozen cells still applied (p0,m0=10 and p1,m2=40)', eq(
    plan.updates.map((u) => u.key).sort(),
    [cellKey('p0', 'm0'), cellKey('p1', 'm2')].sort(),
  ))
  const report = formatPasteReport(plan)
  check('report mentions frozen count', !!report && report.includes('2 cells skipped (frozen)'))
  check('report mentions invalid count', !!report && report.includes('1 cell invalid'))
}

// ── planPaste: >100 cells for confirmation gate ───────────────────────────
console.log('planPaste: >100 cell threshold (caller decides to confirm)')
{
  const bigRows: GridRow[] = Array.from({ length: 20 }, (_, i) => ({ id: `r${i}`, label: `R${i}` }))
  const bigCols: GridCol[] = Array.from({ length: 10 }, (_, i) => ({ id: `c${i}`, label: `C${i}` }))
  const bigBlock = Array.from({ length: 15 }, () => Array.from({ length: 10 }, () => '1').join('\t')).join('\n')
  const plan = planPaste(bigBlock, { rowIdx: 0, colIdx: 0 }, bigRows, bigCols, new Set())
  check('15x10 = 150 totalCells (> 100 -> caller must confirm)', plan.totalCells === 150)
}

// ── planPaste: exact threshold boundary — MatrixGrid.tsx gates on
// `plan.totalCells > CONFIRM_THRESHOLD` (strictly greater), not >=. Pinning
// both sides of that boundary here as a pure-logic check is what's left of
// the MatrixGrid confirmation-gating fix that's expressible outside React —
// the "lock cell edits / ignore a second paste while confirmPlan is open"
// half of that fix is component state (useEffect/disabled-input gating),
// not a pure function, so it isn't and can't be re-verified by this script;
// see MatrixGrid.tsx's `locked` comment for that half instead.
console.log('planPaste: exact 100/101 threshold boundary (MatrixGrid gates on totalCells > 100)')
{
  const boundaryRows: GridRow[] = Array.from({ length: 20 }, (_, i) => ({ id: `br${i}`, label: `BR${i}` }))
  const boundaryCols: GridCol[] = Array.from({ length: 20 }, (_, i) => ({ id: `bc${i}`, label: `BC${i}` }))
  const block10x10 = Array.from({ length: 10 }, () => Array.from({ length: 10 }, () => '1').join('\t')).join('\n')
  const planAt100 = planPaste(block10x10, { rowIdx: 0, colIdx: 0 }, boundaryRows, boundaryCols, new Set())
  check('10x10 = exactly 100 totalCells (boundary: must NOT trigger confirm)', planAt100.totalCells === 100)

  const block10x11 = Array.from({ length: 10 }, () => Array.from({ length: 11 }, () => '1').join('\t')).join('\n')
  const planAt110 = planPaste(block10x11, { rowIdx: 0, colIdx: 0 }, boundaryRows, boundaryCols, new Set())
  check('10x11 = 110 totalCells (just over boundary: must trigger confirm)', planAt110.totalCells === 110)
}

// ── applyPasteUpdates: 0 deletes the sparse entry ─────────────────────────
console.log('applyPasteUpdates')
{
  const base = new Map([[cellKey('p0', 'm0'), 5]])
  const next = applyPasteUpdates(base, [{ key: cellKey('p0', 'm0'), value: 0, rowIdx: 0, colIdx: 0 }])
  check('writing 0 deletes the sparse entry', !next.has(cellKey('p0', 'm0')))
}

// ── selectionToTsv: round-trips back through parseClipboardText ──────────
console.log('selectionToTsv')
{
  const cells = new Map([[cellKey('p0', 'm0'), 100], [cellKey('p0', 'm1'), 200], [cellKey('p1', 'm0'), 300]])
  const tsv = selectionToTsv(rows, cols, cells, { startRow: 0, startCol: 0, endRow: 1, endCol: 1 })
  check('produces 2x2 TSV with blanks for missing cells', tsv === '100\t200\n300\t')
  const reparsed = parseClipboardText(tsv)
  check('re-parses to the same 2x2 shape', eq(reparsed, [['100', '200'], ['300', '']]))
}

// ── history: undo/redo stack ──────────────────────────────────────────────
console.log('history: undo/redo')
{
  let h = initHistory(new Map())
  check('index 0 is pristine, canUndo=false, canRedo=false', h.idx === 0 && !canUndo(h) && !canRedo(h))

  // Commit #1: single edit
  h = commitCells(h, (prev) => new Map(prev).set('a', 1))
  check('after 1 commit: idx=1, canUndo=true', h.idx === 1 && canUndo(h))
  check('current cells = {a:1}', eq([...currentCells(h)], [['a', 1]]))

  // Commit #2: a whole paste block as ONE commit (simulating 12-cell paste)
  h = commitCells(h, (prev) => {
    const m = new Map(prev)
    for (let i = 0; i < 12; i++) m.set(`p${i}`, i)
    return m
  })
  check('after paste commit: exactly one more history entry (stack len=3)', h.stack.length === 3)
  check('current has 13 entries (1 + 12)', currentCells(h).size === 13)

  // Ctrl+Z once reverts the ENTIRE pasted block, not cell-by-cell
  h = undo(h)
  check('one undo reverts the whole 12-cell paste in a single step', currentCells(h).size === 1 && eq([...currentCells(h)], [['a', 1]]))

  // Ctrl+Y redoes it
  h = redo(h)
  check('redo restores all 13 entries', currentCells(h).size === 13)
  check('canRedo=false after redoing to the tip', !canRedo(h))

  // A no-op commit (identical map) must not push a new stack entry
  const before = h.stack.length
  h = commitCells(h, (prev) => new Map(prev))
  check('no-op commit does not grow the stack', h.stack.length === before)

  // Undo then a NEW commit truncates the old redo branch
  h = undo(h)
  h = commitCells(h, (prev) => new Map(prev).set('z', 99))
  check('branching commit truncates stale redo branch', !canRedo(h))
}

console.log('')
if (failures > 0) {
  console.log(`${failures} check(s) FAILED`)
  process.exit(1)
} else {
  console.log('All checks passed.')
}
