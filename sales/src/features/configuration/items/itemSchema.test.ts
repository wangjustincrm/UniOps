import { describe, expect, it } from 'vitest'
import { unitsText } from './itemsApi'
import { BARCODE_MESSAGE, EA_MESSAGE, EXCEL_MESSAGE, UNITS_HINT, itemCreateSchema, itemEmptyValues, parseUnits } from './itemSchema'

const valid = { ...itemEmptyValues, item_code: 'CF0092', units: 'EA=1; CASE=6', upc: '062639300012', gtin: '10062639300019' }
const issues = (r: { error?: { issues: { path: PropertyKey[]; message: string }[] } }) =>
  Object.fromEntries((r.error?.issues ?? []).map((i) => [i.path.join('.'), i.message]))

describe('item form', () => {
  it('units are written UNIT=factor and must include EA=1 (AC-OB-14)', () => {
    expect(parseUnits('EA=1; case=6; 2PACK=2')).toEqual({ units: [
      { unit: 'EA', factor: 1, titles: [] }, { unit: 'CASE', factor: 6, titles: [] }, { unit: '2PACK', factor: 2, titles: [] }] })
    expect(parseUnits('CASE=6').error).toBe(EA_MESSAGE)
    expect(parseUnits('EA=2').error).toBe(EA_MESSAGE)
    expect(parseUnits('EA=1; CASE=6; CASE=12').error).toBe('a unit appears twice')
    expect(parseUnits('EA=1; CASE').error).toMatch(/UNIT=factor/)
    expect(itemCreateSchema.safeParse(valid).success).toBe(true)
  })
  it('a unit may name its Shopify titles in brackets, each for one unit only', () => {
    expect(parseUnits('EA=1, CASE=6 [Case (6 Cans), Case of 6]; 2PACK=2 [2-Can]').units).toEqual([
      { unit: 'EA', factor: 1, titles: [] }, { unit: 'CASE', factor: 6, titles: ['Case (6 Cans)', 'Case of 6'] },
      { unit: '2PACK', factor: 2, titles: ['2-Can'] }])
    expect(unitsText([{ unit: 'EA', factor_ea: '1.0000', shopify_titles: [] },
      { unit: '2PACK', factor_ea: '2.0000', shopify_titles: ['2-Can', '2-Pack'] }])).toBe('EA=1; 2PACK=2 [2-Can, 2-Pack]')
    expect(parseUnits('EA=1; CASE=6 [2-Pack]; 2PACK=2').error).toMatch(/names both 2PACK and CASE/)
    expect(parseUnits('EA=1; CASE=6 [--]').error).toMatch(/no letters or digits/)
    expect(parseUnits('EA=1; CASE=6 [x').error).toBe(UNITS_HINT)
  })
  it('codes and barcodes refuse what Excel does to them', () => {
    expect(issues(itemCreateSchema.safeParse({ ...valid, item_code: '1.22E+11', upc: '62639300012', gtin: '1.00626E+13' }))).toEqual({
      item_code: EXCEL_MESSAGE, upc: BARCODE_MESSAGE, gtin: BARCODE_MESSAGE })
  })
})
