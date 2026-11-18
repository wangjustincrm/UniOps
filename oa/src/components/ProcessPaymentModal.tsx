import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CreditCard, CheckCircle2 } from 'lucide-react'
import { formatAmount } from '@/lib/utils'
import { financeApi } from '@/lib/api'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

import { Button, Modal, Select } from '@uniops/shell'
interface FundingAccount {
  id: string; name: string; bank_name: string; kind: string
  account_masked: string | null; currency: string; is_active: boolean
}

export default function ProcessPaymentModal({
  docNumber, currency, amount, busy, error, onConfirm, onClose,
}: {
  docNumber: string; currency: string; amount: number; busy: boolean
  // The server's refusal (e.g. paying your own claim, PAY-003), shown verbatim.
  error?: string
  onConfirm: (bankAccountId: string) => void; onClose: () => void
}) {
  const [bankId, setBankId] = useState('')
  const { data: accounts = [], isLoading } = useQuery({
    queryKey: ['finance-bank-accounts'],
    queryFn: () => financeApi.get<FundingAccount[]>('/bank/accounts'),
  })
  const options = accounts.filter((a) => a.is_active && a.currency === currency)

  // Closes only by Cancel / the close button, as before (no Escape, no backdrop).
  return (
    <Modal
      open
      onClose={onClose}
      closeOnEscape={false}
      title="Mark as Processed"
      description={`${docNumber} · ${formatAmount(amount, currency)}`}
      icon={<CreditCard className="h-4 w-4" />}
      footer={<>
        <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
        <Button size="sm" disabled={!bankId || busy} onClick={() => onConfirm(bankId)}>
          <CheckCircle2 className="h-4 w-4" />{busy ? 'Processing…' : 'Confirm Payment'}
        </Button>
      </>}
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">Pay from (bank account or credit card) *</label>
          <Select value={bankId} onChange={(e) => setBankId(e.target.value)}
            className="w-full">
            <option value="">Select a {currency} payment source…</option>
            {options.map((a) => (
              <option key={a.id} value={a.id}>
                {a.kind === 'credit_card' ? 'Card' : 'Bank'} · {a.bank_name} — {a.name}{a.account_masked ? ` · …${a.account_masked}` : ''}
              </option>
            ))}
          </Select>
          {!isLoading && options.length === 0 && (
            <p className="text-xs text-warning-fg">No active {currency} accounts. Add one under Finance → Bank &amp; Cards.</p>
          )}
        </div>
        {error && <ErrorBanner message={error} />}
      </div>
    </Modal>
  )
}
