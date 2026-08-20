// Guard: the Production Plan page must actually PASS the optional props the
// matrix reads.
//
// `frozenUntilMonth` and `diffByCell` are optional on ProductionMatrix, so
// forgetting them at the call site type-checks perfectly and the feature
// simply does nothing — which is exactly what happened to the frozen zone:
// the matrix was taught to grey those columns, the page never handed it the
// month, and tsc had nothing to complain about. A greyed column that never
// greys is worse than one that was never built, because it looks done.
//
// Run with the other *.verify.ts checks (see weekColumns.verify.ts).
/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const page = readFileSync(join(here, 'ProductionPlanPage.tsx'), 'utf8')

const REQUIRED = ['frozenUntilMonth=', 'diffByCell=', 'readOnly=', 'weekGrid=']

const missing = REQUIRED.filter((prop) => !page.includes(prop))
if (missing.length > 0) {
  throw new Error(
    `ProductionPlanPage does not pass ${missing.join(', ')} to ProductionMatrix — `
    + 'the matrix reads them, they are optional, and tsc cannot see the omission.',
  )
}

// Self-check: a guard whose detection is broken passes forever and is worse
// than no guard at all.
const fakePage = '<ProductionMatrix readOnly={x} weekGrid={y} />'
const fakeMissing = REQUIRED.filter((prop) => !fakePage.includes(prop))
if (fakeMissing.length !== 2) {
  throw new Error('matrixProps.verify cannot detect a missing prop — fix the guard')
}

console.log('matrixProps.verify: ProductionPlanPage passes every optional matrix prop')
