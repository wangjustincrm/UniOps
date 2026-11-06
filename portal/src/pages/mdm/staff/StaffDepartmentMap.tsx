import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { SortTh, useSortableRows } from '@uniops/shell'
import { mdmApi } from '@/lib/api'
import { INPUT, StaffShell, TD, TH, useStaffPerm, type Department } from './shared'

interface MapRow {
  adp_department: string; employees: number; department_id: string | null
  choose_per_employee: boolean; mapped: boolean
}
type Key = 'adp_department' | 'employees' | 'mapped'
const CHOOSE = '__choose__'

export default function StaffDepartmentMap() {
  const qc = useQueryClient()
  const { can } = useStaffPerm()
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const { data } = useQuery<{ items: MapRow[] }>({
    queryKey: ['staff-department-map'], queryFn: () => mdmApi.get('/staff/department-map'),
  })
  const { data: depts } = useQuery<{ items: Department[] }>({
    queryKey: ['portal-departments'], queryFn: () => mdmApi.get('/departments'),
  })
  const { rows, sort, onSort } = useSortableRows<MapRow, Key>(data?.items ?? [], {
    adp_department: (r) => r.adp_department,
    employees: (r) => r.employees,
    mapped: (r) => (r.mapped ? 1 : 0),
  })

  const save = async (row: MapRow, value: string) => {
    if (busy || !value) return
    setBusy(true); setError(''); setNotice('')
    try {
      const { applied } = await mdmApi.put<{ applied: number }>('/staff/department-map', value === CHOOSE
        ? { adp_department: row.adp_department, department_id: null, choose_per_employee: true }
        : { adp_department: row.adp_department, department_id: value, choose_per_employee: false })
      qc.invalidateQueries({ queryKey: ['staff-department-map'] })
      qc.invalidateQueries({ queryKey: ['staff-import-current'] })
      qc.invalidateQueries({ queryKey: ['staff'] })
      if (applied) setNotice(`${applied} waiting employee(s) now have this department.`)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <StaffShell permission="employee.view">
      <p className="mb-3 text-sm text-neutral-500">
        The department an import gives a new employee, or one whose ADP department changed. Saving also fills it in
        for employees waiting on Needs department. It is a default: a department chosen by hand on an employee stays
        until their ADP department changes.
      </p>
      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}
      {notice && <p className="mb-3 text-sm text-[#085E5E]">{notice}</p>}
      <div className="overflow-hidden rounded-xl border border-neutral-200 bg-white">
        <div className="max-h-[calc(100vh-280px)] overflow-auto">
          <table className="w-full">
            <thead><tr>
              <SortTh label="ADP Department" sortKey="adp_department" sort={sort} onSort={onSort} className={TH} />
              <SortTh label="Employees" sortKey="employees" sort={sort} onSort={onSort} className={TH} />
              <SortTh label="UniOps Department" sortKey="mapped" sort={sort} onSort={onSort} className={TH} />
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.adp_department} className="border-t border-neutral-100">
                  <td className={TD}>{r.adp_department || '(empty)'}</td>
                  <td className={TD}>{r.employees}</td>
                  <td className={TD}>
                    <select className={INPUT} disabled={!can('employee.edit') || busy}
                      aria-label={`UniOps department for ${r.adp_department || 'empty'}`}
                      value={r.choose_per_employee ? CHOOSE : r.department_id ?? ''}
                      onChange={(e) => save(r, e.target.value)}>
                      <option value="">Unmapped</option>
                      <option value={CHOOSE}>Choose per employee</option>
                      {depts?.items.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!data?.items.length && <p className="py-8 text-center text-sm text-neutral-400">ADP departments appear here once a roster has been uploaded.</p>}
        </div>
      </div>
    </StaffShell>
  )
}
