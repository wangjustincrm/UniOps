// Cent-rounded equality. Plain float subtraction of two Number()-coerced decimal
// strings can land a hair off zero (e.g. summing several receipt totals), which
// would otherwise make a genuinely-even match look like it has a variance.
//
// 提取自 InvoiceReceiptsPanel(它自己的注释记录了这段逻辑此前已从 MatchPanel
// 搬过一次)。凡是比较两笔金额是否相等,都用这个,不要写 === 0 / !== 0。
export function centsEqual(a: number, b: number): boolean {
  return Math.round(a * 100) === Math.round(b * 100)
}
