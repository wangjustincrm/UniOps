/**
 * Where a task from the Portal's inbox opens.
 *
 * ★ Round 7, TC-03: the Portal kept its own list of where Safety documents
 * live and covered three of them; every other Safety task — a session to run,
 * a policy to sign — went to a page that does not exist. The Portal now sends
 * every Safety task here, and Safety answers where it belongs, from the same
 * table its own My Actions uses.
 */
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '@/lib/api'

export default function TaskRedirectPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const where = useQuery({
    queryKey: ['task-path', id],
    queryFn: () => api.get<{ path: string }>(`/api/v1/actions/tasks/${encodeURIComponent(id)}/open`),
    enabled: Boolean(id),
    retry: false,
  })

  useEffect(() => {
    if (where.data) navigate(where.data.path, { replace: true })
  }, [where.data, navigate])

  if (where.error) {
    return (
      <div className="p-6 text-center text-sm text-neutral-600">
        <p>{where.error instanceof ApiError && where.error.status === 404
          ? 'This task is no longer in Safety.'
          : 'The task could not be opened.'}</p>
        <Link to="/actions/mine" className="mt-2 inline-block font-semibold text-primary-700 hover:underline">
          Go to My actions
        </Link>
      </div>
    )
  }
  return <p className="p-6 text-center text-sm text-neutral-500">Opening the task…</p>
}
