import { useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { CreditCard, CheckCircle2, X } from 'lucide-react'
import { formatAmount } from '@/lib/utils'
import { financeApi } from '@/lib/api'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

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

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-inverse/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl bg-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-line-subtle px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary-50">
              <CreditCard className="h-5 w-5 text-primary-600" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-fg">Mark as Processed</h2>
              <p className="text-xs text-fg-muted">{docNumber} · {formatAmount(amount, currency)}</p>
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-fg-muted hover:bg-surface-sunken"><X className="h-4 w-4" /></button>
        </div>
        <div className="px-6 py-5 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-fg-secondary">Pay from (bank account or credit card) *</label>
            <select value={bankId} onChange={(e) => setBankId(e.target.value)}
              className="w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600">
              <option value="">Select a {currency} payment source…</option>
              {options.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.kind === 'credit_card' ? '💳' : '🏦'} {a.bank_name} — {a.name}{a.account_masked ? ` · …${a.account_masked}` : ''}
                </option>
              ))}
            </select>
            {!isLoading && options.length === 0 && (
              <p className="text-xs text-warning-fg">No active {currency} accounts. Add one under Finance → Bank &amp; Cards.</p>
            )}
          </div>
          {error && <ErrorBanner message={error} />}
          <div className="flex justify-end gap-2">
            <button onClick={onClose} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line px-3 text-sm font-medium text-fg-secondary hover:bg-surface-subtle">Cancel</button>
            <button disabled={!bankId || busy} onClick={() => onConfirm(bankId)}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary-600 px-3 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-40 disabled:cursor-not-allowed">
              <CheckCircle2 className="h-4 w-4" />{busy ? 'Processing…' : 'Confirm Payment'}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
