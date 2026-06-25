export interface DemoVendor {
  id: string
  poid: string
  name: string
  email: string
  contact?: string
}

export const DEMO_VENDORS: DemoVendor[] = [
  { id: 'v1', poid: 'ABC', name: 'ABC Supplies Ltd',          email: 'orders@abcsupplies.ca',       contact: 'Tom Richards' },
  { id: 'v2', poid: 'XYZ', name: 'XYZ Corp',                  email: 'procurement@xyzcorp.ca',       contact: 'Linda Park' },
  { id: 'v3', poid: 'PQR', name: 'PQR Materials Inc',         email: 'sales@pqrmaterials.ca',        contact: 'James Wu' },
  { id: 'v4', poid: 'DEL', name: 'Dell Canada',               email: 'b2b.orders@dell.com',          contact: 'Dell B2B Team' },
  { id: 'v5', poid: 'SAL', name: 'Salesforce Canada',         email: 'orders@salesforce.com',        contact: 'Account Manager' },
  { id: 'v6', poid: 'HAB', name: 'Habasit Canada',            email: 'sales.ca@habasit.com',         contact: 'Marie Tremblay' },
  { id: 'v7', poid: 'GRU', name: 'Grundfos Canada',           email: 'ca.orders@grundfos.com',       contact: 'Service Desk' },
]

export function getVendorByPoid(poid: string): DemoVendor | undefined {
  return DEMO_VENDORS.find((v) => v.poid === poid)
}
