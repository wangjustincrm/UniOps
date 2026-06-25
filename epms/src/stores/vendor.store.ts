import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface Vendor {
  id: string
  code: string           // POID short code e.g. "ABC"
  name: string
  category: string       // e.g. "Raw Materials", "Services", "IT"
  contactName: string
  contactEmail: string
  phone: string
  address: string
  paymentTerms: 'net15' | 'net30' | 'net60' | 'net90' | 'cod' | 'prepayment'
  currency: string       // CAD, USD, etc.
  active: boolean
  notes: string
  createdAt: string
}

const DEMO_VENDORS: Vendor[] = [
  {
    id: 'vnd01',
    code: 'NXS',
    name: 'Nexus Industrial Supply Co.',
    category: 'Raw Materials',
    contactName: 'Marie-Claire Beaumont',
    contactEmail: 'mcbeaumont@nexusindustrial.ca',
    phone: '(416) 555-0182',
    address: '1500 Lawrence Ave E, Toronto, ON M4A 2X4',
    paymentTerms: 'net30',
    currency: 'CAD',
    active: true,
    notes: 'Primary supplier for steel stock and sheet metal.',
    createdAt: '2026-01-10',
  },
  {
    id: 'vnd02',
    code: 'TCS',
    name: 'TechCore Solutions Inc.',
    category: 'IT',
    contactName: 'Rajesh Gupta',
    contactEmail: 'r.gupta@techcoresolutions.ca',
    phone: '(604) 555-0247',
    address: '900 West Georgia St, Suite 1200, Vancouver, BC V6C 2W6',
    paymentTerms: 'net30',
    currency: 'CAD',
    active: true,
    notes: 'IT hardware, networking equipment, and software licenses.',
    createdAt: '2026-01-10',
  },
  {
    id: 'vnd03',
    code: 'AMS',
    name: 'Apex Maintenance Services Ltd.',
    category: 'Maintenance',
    contactName: 'Derek Olafsson',
    contactEmail: 'dolafsson@apexmaint.ca',
    phone: '(780) 555-0314',
    address: '4220 Gateway Blvd, Edmonton, AB T6J 5C2',
    paymentTerms: 'net60',
    currency: 'CAD',
    active: true,
    notes: 'HVAC, electrical, and general facility maintenance contracts.',
    createdAt: '2026-01-12',
  },
  {
    id: 'vnd04',
    code: 'PNO',
    name: 'Pinnacle Office Outfitters',
    category: 'Office Supplies',
    contactName: 'Sandra Thibodeau',
    contactEmail: 's.thibodeau@pinnacleoffice.ca',
    phone: '(514) 555-0398',
    address: '250 René-Lévesque Blvd W, Montréal, QC H2Z 1A4',
    paymentTerms: 'net15',
    currency: 'CAD',
    active: true,
    notes: 'Office furniture, stationery, and consumables.',
    createdAt: '2026-01-15',
  },
  {
    id: 'vnd05',
    code: 'NLG',
    name: 'Northern Logistics Partners',
    category: 'Logistics',
    contactName: 'James Fitzpatrick',
    contactEmail: 'j.fitzpatrick@nlp-canada.com',
    phone: '(905) 555-0421',
    address: '6800 Century Ave, Mississauga, ON L5N 2W5',
    paymentTerms: 'net30',
    currency: 'CAD',
    active: true,
    notes: 'Freight, warehousing, and last-mile delivery across Ontario.',
    createdAt: '2026-01-18',
  },
  {
    id: 'vnd06',
    code: 'CPS',
    name: 'CloudPeak Software Corp.',
    category: 'IT',
    contactName: 'Anika Patel',
    contactEmail: 'apatel@cloudpeaksw.ca',
    phone: '(403) 555-0509',
    address: '888 3rd St SW, Calgary, AB T2P 5C5',
    paymentTerms: 'prepayment',
    currency: 'USD',
    active: true,
    notes: 'SaaS subscriptions — ERP modules and collaboration tools.',
    createdAt: '2026-01-20',
  },
  {
    id: 'vnd07',
    code: 'GRS',
    name: 'Great Rivers Consulting Group',
    category: 'Services',
    contactName: 'Luc Tremblay',
    contactEmail: 'l.tremblay@greatriverscg.ca',
    phone: '(613) 555-0611',
    address: '150 Elgin St, Ottawa, ON K2P 1L4',
    paymentTerms: 'net60',
    currency: 'CAD',
    active: false,
    notes: 'Management consulting — contract ended Q4 2025.',
    createdAt: '2026-01-22',
  },
  {
    id: 'vnd08',
    code: 'MRP',
    name: 'Maple Ridge Packaging Ltd.',
    category: 'Raw Materials',
    contactName: 'Christine Laroche',
    contactEmail: 'claroche@mapleridgepkg.ca',
    phone: '(604) 555-0738',
    address: '2200 No. 5 Road, Richmond, BC V6X 2T1',
    paymentTerms: 'net30',
    currency: 'CAD',
    active: true,
    notes: 'Corrugated packaging, stretch film, and pallet wrap.',
    createdAt: '2026-01-25',
  },
]

interface VendorStoreState {
  vendors: Vendor[]
  addVendor: (data: Omit<Vendor, 'id' | 'createdAt'>) => Vendor
  updateVendor: (id: string, patch: Partial<Omit<Vendor, 'id' | 'createdAt'>>) => void
  deleteVendor: (id: string) => void
  toggleActive: (id: string) => void
}

export const useVendorStore = create<VendorStoreState>()(
  persist(
    (set) => ({
      vendors: DEMO_VENDORS,

      addVendor: (data) => {
        const newVendor: Vendor = {
          ...data,
          id: crypto.randomUUID(),
          createdAt: new Date().toISOString().slice(0, 10),
        }
        set((s) => ({ vendors: [...s.vendors, newVendor] }))
        return newVendor
      },

      updateVendor: (id, patch) => {
        set((s) => ({
          vendors: s.vendors.map((v) => (v.id === id ? { ...v, ...patch } : v)),
        }))
      },

      deleteVendor: (id) => {
        set((s) => ({ vendors: s.vendors.filter((v) => v.id !== id) }))
      },

      toggleActive: (id) => {
        set((s) => ({
          vendors: s.vendors.map((v) =>
            v.id === id ? { ...v, active: !v.active } : v
          ),
        }))
      },
    }),
    { name: 'epms-vendors' }
  )
)
