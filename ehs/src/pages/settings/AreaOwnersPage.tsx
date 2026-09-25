/**
 * Who covers which part of the plant.
 *
 * Work given to a role reaches everybody holding it, and for area supervisors
 * that was one plant-wide queue: an unassigned equipment check appeared on
 * every supervisor's list at once. What was missing is this — the fact nobody
 * had written down anywhere.
 *
 * An area carries everything under it. Naming somebody for "103 – Dryer" names
 * them for Line 1 and Line 2 too, which is how the plant is talked about and
 * which means the list does not go stale the day a line is added.
 *
 * Grouped by area rather than by person, because the question this page is
 * opened with is "who has the dryer" far more often than "what has Ken got".
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { MapPin, Trash2, UserPlus } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import {
  Card, Empty, ErrorNote, Field, LoadFailedNote, PageHeader, primaryButton, SectionTitle,
} from '@/components/ui'
import { LocationSelect } from '@/components/LocationSelect'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import { usePermissions } from '@/hooks/usePermissions'

interface AreaOwner {
  id: string
  user_id: string
  user_name: string | null
  location_id: string
  location_path: string
}

interface Unattributed { count: number; examples: string[] }

export default function AreaOwnersPage() {
  const { can } = usePermissions()
  const mayManage = can('ehs.settings.manage')
  const queryClient = useQueryClient()
  const [person, setPerson] = useState<PersonValue>(EMPTY_PERSON)
  const [locationId, setLocationId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const owners = useQuery({
    queryKey: ['area-owners'],
    queryFn: () => api.get<AreaOwner[]>('/api/v1/settings/area-owners'),
  })
  // How much work is waiting on this page being finished.
  const orphans = useQuery({
    queryKey: ['unattributed'],
    queryFn: () => api.get<Unattributed>('/api/v1/actions/unattributed'),
  })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['area-owners'] })
    queryClient.invalidateQueries({ queryKey: ['unattributed'] })
    queryClient.invalidateQueries({ queryKey: ['actions', 'mine', 'tasks'] })
  }

  const add = useMutation({
    mutationFn: () => api.post('/api/v1/settings/area-owners', {
      user_id: person.userId, location_id: locationId,
    }),
    onSuccess: () => {
      setPerson(EMPTY_PERSON)
      setLocationId(null)
      setError(null)
      refresh()
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/api/v1/settings/area-owners/${id}`),
    onSuccess: refresh,
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  const rows = owners.data ?? []
  const byArea = new Map<string, AreaOwner[]>()
  for (const r of rows) {
    byArea.set(r.location_path, [...(byArea.get(r.location_path) ?? []), r])
  }

  return (
    <div className="p-4">
      <PageHeader
        title="Area responsibility"
        subtitle="Who covers which part of the plant. An area includes everything underneath it."
      />

      {owners.isError && <LoadFailedNote what="The area list" />}

      {(orphans.data?.count ?? 0) > 0 && (
        <div className="mb-3 rounded-lg bg-warning-50 px-3 py-2.5">
          <p className="text-sm font-semibold text-warning-800">
            {orphans.data!.count}{' '}
            {orphans.data!.count === 1 ? 'task belongs' : 'tasks belong'} to a part of
            the plant nobody is down for.
          </p>
          <p className="mt-1 text-xs text-warning-700">
            They are on the HSE list in the meantime. Naming somebody for the area
            moves them.
          </p>
          <ul className="mt-1.5 list-inside list-disc text-xs text-warning-700">
            {orphans.data!.examples.map((e) => <li key={e}>{e}</li>)}
          </ul>
        </div>
      )}

      {mayManage && (
        <Card className="mb-4">
          <SectionTitle className="mb-2">Make somebody responsible</SectionTitle>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Who">
              <PersonPicker value={person} onChange={setPerson} />
            </Field>
            <Field label="Which area"
                   hint="Everything underneath it comes with it">
              {/* The picker's default prompt is "Where did it happen?", which
                  is right on an incident form and wrong here. */}
              <LocationSelect value={locationId} onChange={setLocationId}
                              placeholder="Pick an area" />
            </Field>
          </div>
          {error && <ErrorNote error={new Error(error)} />}
          <button type="button" className={`${primaryButton} mt-3`}
                  disabled={!person.userId || !locationId || add.isPending}
                  onClick={() => {
                    if (person.userId && locationId && !add.isPending) add.mutate()
                  }}>
            <UserPlus className="h-4 w-4" aria-hidden />
            {add.isPending ? 'Saving…' : 'Make responsible'}
          </button>
          {!person.userId && person.name.trim() !== '' && (
            <p className="mt-2 text-xs text-neutral-500">
              Only people with a UniOps account can be made responsible for an area —
              the work has to reach somebody's list.
            </p>
          )}
        </Card>
      )}

      {rows.length === 0 ? (
        <Empty>
          Nobody is down for any area yet, so work given to area supervisors is
          going to HSE. Assign the areas above.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {[...byArea.entries()].map(([path, people]) => (
            <li key={path} className="rounded-xl border border-neutral-200 bg-white p-3.5">
              <p className="flex items-center gap-1.5 text-sm font-semibold text-neutral-900">
                <MapPin className="h-4 w-4 shrink-0 text-primary-600" aria-hidden />
                {path.split('/').join(' › ')}
              </p>
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {people.map((r) => (
                  <li key={r.id}
                      className="flex items-center gap-1.5 rounded-full bg-neutral-100 px-2.5 py-1 text-xs text-neutral-700">
                    {r.user_name ?? 'Somebody who has left'}
                    {mayManage && (
                      <button type="button"
                              onClick={() => { if (!remove.isPending) remove.mutate(r.id) }}
                              className="flex min-h-[24px] min-w-[24px] items-center justify-center text-neutral-400 hover:text-danger-700"
                              aria-label={`Remove ${r.user_name ?? 'this person'} from ${path}`}>
                        <Trash2 className="h-3.5 w-3.5" aria-hidden />
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
