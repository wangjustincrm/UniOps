import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, Loader2, AlertTriangle } from 'lucide-react'
import { api } from '@/lib/api'

interface Policy {
  id: string
  hst_rate: number
  mileage_rate_per_km: number
  mileage_budget_account_id: string | null
  max_km_per_claim: number
  meal_breakfast_limit: number
  meal_lunch_limit: number
  meal_dinner_limit: number
  meal_incidental_limit: number
  custom_forms: unknown[]
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-neutral-600">{label}</label>
      {children}
      {hint && <p className="mt-1 text-[11px] text-neutral-400">{hint}</p>}
    </div>
  )
}

function NumberInput({
  value, onChange, step = '0.01', min = '0',
}: {
  value: string; onChange: (v: string) => void; step?: string; min?: string
}) {
  return (
    <input
      type="number" min={min} step={step} value={value}
      onChange={e => onChange(e.target.value)}
      className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm text-right focus:outline-none focus:border-primary-400"
    />
  )
}

export default function ExpenseConfigPage() {
  const qc = useQueryClient()
  const { data: policy, isLoading } = useQuery<Policy>({
    queryKey: ['expense-policy'],
    queryFn: () => api.get<Policy>('/api/v1/policy'),
  })

  const [hst, setHst] = useState('')
  const [milRate, setMilRate] = useState('')
  const [maxKm, setMaxKm] = useState('')
  const [breakfast, setBreakfast] = useState('')
  const [lunch, setLunch] = useState('')
  const [dinner, setDinner] = useState('')
  const [incidental, setIncidental] = useState('')
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!policy) return
    setHst(String(policy.hst_rate))
    setMilRate(String(policy.mileage_rate_per_km))
    setMaxKm(String(policy.max_km_per_claim))
    setBreakfast(String(policy.meal_breakfast_limit))
    setLunch(String(policy.meal_lunch_limit))
    setDinner(String(policy.meal_dinner_limit))
    setIncidental(String(policy.meal_incidental_limit ?? 17.30))
  }, [policy])

  const mutation = useMutation({
    mutationFn: (body: object) => api.patch('/api/v1/policy', body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['expense-policy'] })
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    },
    onError: (e: any) => setError(e.message || 'Save failed'),
  })

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    mutation.mutate({
      hst_rate: parseFloat(hst),
      mileage_rate_per_km: parseFloat(milRate),
      max_km_per_claim: parseInt(maxKm),
      meal_breakfast_limit: parseFloat(breakfast),
      meal_lunch_limit: parseFloat(lunch),
      meal_dinner_limit: parseFloat(dinner),
      meal_incidental_limit: parseFloat(incidental),
    })
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-6 w-6 animate-spin text-neutral-400" />
      </div>
    )
  }

  return (
    <form onSubmit={handleSave} className="flex flex-col gap-8 max-w-lg">
      <div>
        <h1 className="text-2xl font-bold text-neutral-900">Expense Configuration</h1>
        <p className="mt-0.5 text-sm text-neutral-500">
          Rates and limits applied to all expense claims. Changes take effect immediately on new submissions.
        </p>
      </div>

      {/* Tax */}
      <section className="flex flex-col gap-4">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-500 border-b border-neutral-100 pb-2">
          Tax
        </h2>
        <Field label="HST / GST Rate" hint="Enter as decimal — e.g. 0.13 for 13%">
          <NumberInput value={hst} onChange={setHst} step="0.001" />
        </Field>
      </section>

      {/* Mileage */}
      <section className="flex flex-col gap-4">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-500 border-b border-neutral-100 pb-2">
          Mileage (MIL)
        </h2>
        <Field label="Rate per km (CAD)" hint="CRA prescribed rate; applied to all mileage claims">
          <NumberInput value={milRate} onChange={setMilRate} step="0.001" />
        </Field>
        <Field label="Max km per claim" hint="Claims exceeding this are blocked at creation">
          <NumberInput value={maxKm} onChange={setMaxKm} step="1" min="1" />
        </Field>
      </section>

      {/* Travel meal limits */}
      <section className="flex flex-col gap-4">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-500 border-b border-neutral-100 pb-2">
          Travel Meal Limits per Day (TRV) — CAD
        </h2>
        <p className="text-xs text-neutral-400">
          Meal rows exceeding these limits trigger Finance Manager approval. Limits are per-meal, not per-day total.
        </p>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Breakfast">
            <NumberInput value={breakfast} onChange={setBreakfast} />
          </Field>
          <Field label="Lunch">
            <NumberInput value={lunch} onChange={setLunch} />
          </Field>
          <Field label="Dinner">
            <NumberInput value={dinner} onChange={setDinner} />
          </Field>
          <Field label="Incidental">
            <NumberInput value={incidental} onChange={setIncidental} />
          </Field>
        </div>
      </section>

      {error && (
        <div className="flex items-center gap-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
          <AlertTriangle className="h-4 w-4 shrink-0" />{error}
        </div>
      )}

      <div className="flex items-center gap-3">
        <button type="submit" disabled={mutation.isPending}
          className="flex items-center gap-2 rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50 transition-colors">
          {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          Save Configuration
        </button>
        {saved && (
          <span className="flex items-center gap-1.5 text-sm text-green-600">
            <Check className="h-4 w-4" /> Saved
          </span>
        )}
      </div>
    </form>
  )
}
