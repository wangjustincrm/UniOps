# features/orders

Orders (P3): `OrdersPage` (every order, with the derived state and the detail: lines in EA, source documents, timeline), with system views (All, Needs review — the old Review worklist, now also at /review —, Payment hold, Pending, Archive suppressed, Historical) and "Re-check" on Review orders in the list and the detail (`sales.order.operate`), `ManualOrderPage` (Draft → Submit with the EA preview, FR-OB-19a). Built from `@/platform-ui` only. 940s, shipments and invoices join the detail in P4–P6.
