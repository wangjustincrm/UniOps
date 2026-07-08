import type { RouteDef } from '@uniops/shell'
import RoomsPage from '@/pages/RoomsPage'
import RoomDetailPage from '@/pages/RoomDetailPage'
import BookingCreatePage from '@/pages/BookingCreatePage'
import MyBookingsPage from '@/pages/MyBookingsPage'
import BookingEditPage from '@/pages/BookingEditPage'

// Placeholder page components — Task 16 fills these in.
function AdminRoomsPage() { return <div className="p-6 text-sm text-muted-foreground">Rooms Admin — Task 16</div> }
function AdminAllBookingsPage() { return <div className="p-6 text-sm text-muted-foreground">All Bookings — Task 16</div> }
function AdminNotificationsPage() { return <div className="p-6 text-sm text-muted-foreground">Notifications — Task 16</div> }
function AdminSettingsPage() { return <div className="p-6 text-sm text-muted-foreground">Settings — Task 16</div> }

export const bookingRoutes: RouteDef[] = [
  { path: '/rooms',               element: <RoomsPage />,             tab: { title: 'Rooms',          icon: 'DoorOpen',    keyStrategy: 'static', pinned: true } },
  { path: '/rooms/:id/book',      element: <BookingCreatePage />,     tab: { title: (p) => `Book ${p.id}`, icon: 'CalendarPlus', keyStrategy: 'param', paramName: 'id' } },
  { path: '/rooms/:id',           element: <RoomDetailPage />,        tab: { title: (p) => `Room ${p.id}`, icon: 'DoorOpen',     keyStrategy: 'param', paramName: 'id' } },
  { path: '/my',                  element: <MyBookingsPage />,        tab: { title: 'My Bookings',    icon: 'CalendarDays', keyStrategy: 'static' } },
  { path: '/my/:id/edit',         element: <BookingEditPage />,       tab: { title: 'Edit Booking',   icon: 'Pencil',       keyStrategy: 'param',  paramName: 'id' } },
  { path: '/admin/rooms',         element: <AdminRoomsPage />,        tab: { title: 'Rooms Admin',    icon: 'Wrench',      keyStrategy: 'static' } },
  { path: '/admin/bookings',      element: <AdminAllBookingsPage />,  tab: { title: 'All Bookings',   icon: 'LayoutList',  keyStrategy: 'static' } },
  { path: '/admin/notifications', element: <AdminNotificationsPage />,tab: { title: 'Notifications',  icon: 'BellRing',    keyStrategy: 'static' } },
  { path: '/admin/settings',      element: <AdminSettingsPage />,     tab: { title: 'Settings',       icon: 'Settings',    keyStrategy: 'static' } },
]
