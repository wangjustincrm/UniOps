import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { OpenRecordProvider } from '@/platform-ui'

/** platform-ui's openRecord / openView / goTo, with this app's router (inside a Router). */
export function RouterOpenRecord({ children }: { children?: ReactNode }) {
  return <OpenRecordProvider navigate={useNavigate()}>{children}</OpenRecordProvider>
}
