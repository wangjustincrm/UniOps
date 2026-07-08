import type { RouteDef } from '@uniops/shell'
import RoomsPage from '@/pages/RoomsPage'

// Placeholder page components — Tasks 14–16 fill these in.
function MyBookingsPage() { return <div className="p-6 text-sm text-muted-foreground">My Bookings — Task 13</div> }
function AdminRoomsPage() { return <div className="p-6 text-sm text-muted-foreground">Rooms Admin — Task 14</div> }
function AdminAllBookingsPage() { return <div className="p-6 text-sm text-muted-foreground">All Bookings — Task 14</div> }
function AdminNotificationsPage() { return <div className="p-6 text-sm text-muted-foreground">Notifications — Task 14</div> }
function AdminSettingsPage() { return <div className="p-6 text-sm text-muted-foreground">Settings — Task 14</div> }

export const bookingRoutes: RouteDef[] = [
  { path: '/rooms',               element: <RoomsPage />,             tab: { title: 'Rooms',          icon: 'DoorOpen',    keyStrategy: 'static', pinned: true } },
  { path: '/my',                  element: <MyBookingsPage />,        tab: { title: 'My Bookings',    icon: 'CalendarDays', keyStrategy: 'static' } },
  { path: '/admin/rooms',         element: <AdminRoomsPage />,        tab: { title: 'Rooms Admin',    icon: 'Wrench',      keyStrategy: 'static' } },
  { path: '/admin/bookings',      element: <AdminAllBookingsPage />,  tab: { title: 'All Bookings',   icon: 'LayoutList',  keyStrategy: 'static' } },
  { path: '/admin/notifications', element: <AdminNotificationsPage />,tab: { title: 'Notifications',  icon: 'BellRing',    keyStrategy: 'static' } },
  { path: '/admin/settings',      element: <AdminSettingsPage />,     tab: { title: 'Settings',       icon: 'Settings',    keyStrategy: 'static' } },
]
