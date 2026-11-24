export const MANAGE = 'sales.transfer.manage'
export const CANCELLABLE = ['Draft', 'Ready To Send', '943 Generated', 'Failed']
export const AMENDABLE = ['Waiting for 997', 'Waiting for 944', 'Failed']
export const errorMessages: Record<string, string> = {
  'transfer.not_cancellable': 'The 943 is sent: the transfer can no longer be cancelled.',
  'transfer.same_warehouse': 'The two warehouses must differ.',
  'entity.stale': 'Someone else changed this transfer: it is read again here. Look, then decide again.',
  'transfer.not_failed': 'Only a Failed transfer is resent.',
  'auth.forbidden': 'You do not have permission to manage transfers.',
  'transfer.not_on_hold': 'This transfer is not On Hold any more.',
  'transfer.not_amendable': 'A 944 has answered this transfer: settle the difference instead.',
  'transfer.not_discrepant': 'This transfer has no receipt difference to settle any more.',
  'transfer.item': 'An item is not active in the Item Master.',
}
