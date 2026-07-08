import type { RouteDef } from '@uniops/shell'

// Placeholder page components — Tasks 13–16 fill these in.
function RoomsPage() { return <div className="p-6 text-sm text-muted-foreground">Rooms — Task 13</div> }
function MyBookingsPage() { return <div className="p-6 text-sm text-muted-foreground">My Bookings — Task 13</div> }
function AdminRoomsPage() { return <div className="p-6 text-sm text-muted-foreground">Rooms Admin — Task 14</div> }
function AdminAllBookingsPage() { return <div className="p-6 text-sm text-muted-foreground">All Bookings — Task 14</div> }
function AdminNotificationsPage() { return <div className="p-6 text-sm text-muted-foreground">Notifications — Task 14</div> }
function AdminSettingsPage() { return <div className="p-6 text-sm text-muted-foreground">Settings — Task 14</div> }

export const bookingRoutes: RouteDef[] = [
  { path: '/rooms',               element: <RoomsPage />,             tab: { title: 'Rooms',          icon: 'DoorOpen',    keyStrategy: 'static', pinned: true } },
  { path: '/my',                  element: <MyBookingsPage />,        tab: { title: 'My Bookings',    icon: 'CalendarDays', keyStrategy: 'static' } },
  { path: '/admin/rooms',         element: <AdminRoomsPage />,        tab: { title: 'Rooms Admin',    icon: 'DoorOpen',    keyStrategy: 'static' } },
  { path: '/admin/bookings',      element: <AdminAllBookingsPage />,  tab: { title: 'All Bookings',   icon: 'LayoutList',  keyStrategy: 'static' } },
  { path: '/admin/notifications', element: <AdminNotificationsPage />,tab: { title: 'Notifications',  icon: 'BellRing',    keyStrategy: 'static' } },
  { path: '/admin/settings',      element: <AdminSettingsPage />,     tab: { title: 'Settings',       icon: 'Settings',    keyStrategy: 'static' } },
]
