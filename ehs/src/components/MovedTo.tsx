/** A page that has moved: replaces this address with its new one. */
import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

export default function MovedTo({ to }: { to: string }) {
  const navigate = useNavigate()
  useEffect(() => { navigate(to, { replace: true }) }, [navigate, to])
  return null
}
