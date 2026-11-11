/** Plant wall clock → instant: a date + "HH:MM" typed into a form is the
 *  PLANT's clock (company time zone), not the browser's. The implementation
 *  is shared (@uniops/shell wallClockToISO); this name is kept for callers. */
export { wallClockToISO as plantWallClockToISO } from '@/lib/utils'
