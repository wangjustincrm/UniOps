export const ADJUST = 'sales.inventory.adjust'
export const errorMessages: Record<string, string> = {
  'stock_count.file': "This file cannot be read as Radiant's inventory report.",
  'stock_count.when': 'Give the moment the report was taken (not in the future).',
  'stock_count.applied': 'Undo the count first.',
  'auth.forbidden': 'You may not change the inventory.',
}
