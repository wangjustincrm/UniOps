import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface Part {
  id: string
  code: string
  category: string
  name: string
  description?: string
  supplier: string
  supplierPartNo: string
  supplierItemId?: string   // e.g. Amazon ASIN, supplier catalog #
  unitPrice: number
  unit: string
  isActive: boolean
  createdAt: string
  imageDataUrl?: string | null  // optional base64 image
}

// Demo spare parts catalog — dairy / food processing plant
const DEMO_PARTS: Part[] = [
  {
    id: 'pt01', code: 'PART-0001', category: 'Bearings',
    name: 'Deep Groove Ball Bearing 6205-2RS',
    description: 'Sealed deep groove ball bearing, 25mm bore × 52mm OD × 15mm',
    supplier: 'SKF Canada', supplierPartNo: '6205-2RS/C3',
    supplierItemId: 'B00004RFRN',
    unitPrice: 12.50, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt02', code: 'PART-0002', category: 'Bearings',
    name: 'Cylindrical Roller Bearing NU205ET',
    description: 'Single row, 25mm bore × 52mm OD × 15mm',
    supplier: 'NSK Canada', supplierPartNo: 'NU205ET2X',
    unitPrice: 38.00, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt03', code: 'PART-0003', category: 'Bearings',
    name: 'Tapered Roller Bearing 30206',
    description: '30mm bore × 62mm OD × 17.25mm, single row',
    supplier: 'Timken Canada', supplierPartNo: '30206',
    supplierItemId: 'B000C59HMW',
    unitPrice: 28.00, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt04', code: 'PART-0004', category: 'Seals & Gaskets',
    name: 'Shaft Oil Seal TC 25×42×8mm',
    supplier: 'Parker Hannifin', supplierPartNo: '25-42-8-TC',
    supplierItemId: 'B001GFUV3K',
    unitPrice: 4.20, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt05', code: 'PART-0005', category: 'Seals & Gaskets',
    name: 'O-Ring EPDM 50×3mm (FDA Grade)',
    description: 'Food-grade EPDM, suitable for CIP/SIP cleaning',
    supplier: 'Trelleborg Sealing', supplierPartNo: 'OR-50-3-EPDM-FDA',
    unitPrice: 1.80, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt06', code: 'PART-0006', category: 'Seals & Gaskets',
    name: 'Tri-Clamp Gasket 2" EPDM FDA',
    description: 'White EPDM gasket for 2" tri-clamp fittings, FDA compliant',
    supplier: 'Alfa Laval', supplierPartNo: 'GALFLEX-2-EPDM',
    unitPrice: 3.50, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt07', code: 'PART-0007', category: 'Filters',
    name: 'Hydraulic Filter Element 10μm',
    supplier: 'Pall Corporation', supplierPartNo: 'HC6400FKS16H',
    unitPrice: 65.00, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt08', code: 'PART-0008', category: 'Filters',
    name: 'Air Compressor Filter 40μm G3/4"',
    supplier: 'SMC Corporation', supplierPartNo: 'AFF30C-03-T',
    unitPrice: 42.00, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt09', code: 'PART-0009', category: 'Drive Components',
    name: 'V-Belt Classical A68',
    supplier: 'Gates Canada', supplierPartNo: 'A68',
    supplierItemId: 'B00002N9RV',
    unitPrice: 18.50, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt10', code: 'PART-0010', category: 'Drive Components',
    name: 'Roller Chain No.40 × 10ft',
    supplier: 'Tsubaki Canada', supplierPartNo: '40-1RX10FT',
    unitPrice: 52.00, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt11', code: 'PART-0011', category: 'Pneumatics',
    name: 'Pneumatic Cylinder Ø50 Stroke 100mm',
    supplier: 'Festo Canada', supplierPartNo: 'DNC-50-100-PPV-A',
    unitPrice: 185.00, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt12', code: 'PART-0012', category: 'Pneumatics',
    name: '5/2 Solenoid Valve G1/4" 24VDC',
    supplier: 'SMC Corporation', supplierPartNo: 'SY5120-5LZD-01',
    unitPrice: 95.00, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt13', code: 'PART-0013', category: 'Electrical',
    name: 'Inductive Proximity Sensor M12 NPN 2mm',
    supplier: 'Pepperl+Fuchs', supplierPartNo: 'NBN4-12GM40-E2',
    unitPrice: 45.00, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt14', code: 'PART-0014', category: 'Electrical',
    name: 'Miniature Circuit Breaker 3P 32A Type C',
    supplier: 'Schneider Electric', supplierPartNo: 'A9F74332',
    unitPrice: 78.00, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt15', code: 'PART-0015', category: 'Lubrication',
    name: 'Food Grade Grease NSF H1 — 400g Cartridge',
    description: 'Klüber Paraliq GTE 703, approved for incidental food contact',
    supplier: 'Klüber Lubrication', supplierPartNo: 'PARALIQ-GTE-703-400G',
    unitPrice: 32.00, unit: 'cartridge', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt16', code: 'PART-0016', category: 'Sanitary Fittings',
    name: 'Tri-Clamp 316L 2" Clamp',
    supplier: 'Alfa Laval', supplierPartNo: 'ALC-2-316L',
    unitPrice: 12.00, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt17', code: 'PART-0017', category: 'Sanitary Fittings',
    name: 'Sanitary Valve Seat 1.5" EPDM',
    supplier: 'GEA Group', supplierPartNo: 'SVS-15-EPDM',
    unitPrice: 8.50, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt18', code: 'PART-0018', category: 'Conveyor',
    name: 'Conveyor Belt PVC 500mm × 5m White',
    description: 'Food-grade PVC, smooth surface, FDA compliant',
    supplier: 'Habasit Canada', supplierPartNo: 'F-2EX-500-5M',
    unitPrice: 280.00, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt19', code: 'PART-0019', category: 'Conveyor',
    name: 'Conveyor Idler Roller Ø50 L=500mm',
    supplier: 'Rexnord Canada', supplierPartNo: 'IDR-50-500',
    unitPrice: 35.00, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
  {
    id: 'pt20', code: 'PART-0020', category: 'Pumps & Valves',
    name: 'Centrifugal Pump Impeller Ø120mm',
    description: 'Stainless steel 316L impeller for CRM-P02 pump',
    supplier: 'Grundfos Canada', supplierPartNo: 'IMP-CM5-GR-120',
    unitPrice: 145.00, unit: 'pcs', isActive: true, createdAt: '2026-01-15',
  },
]

// Derive unique categories from parts list
export function getCategories(parts: Part[]): string[] {
  return [...new Set(parts.map((p) => p.category))].sort()
}

// Generate next part code suggestion
export function nextPartCode(parts: Part[]): string {
  const nums = parts
    .map((p) => parseInt(p.code.replace('PART-', ''), 10))
    .filter((n) => !isNaN(n))
  const next = nums.length > 0 ? nums.reduce((a, b) => (b > a ? b : a), 0) + 1 : 1
  return `PART-${String(next).padStart(4, '0')}`
}

// Parse CSV text into Part records (returns parts + error count)
export function parsePartsCsv(csv: string): { parts: Omit<Part, 'id' | 'createdAt'>[]; errors: number } {
  const lines = csv.trim().split('\n')
  const parts: Omit<Part, 'id' | 'createdAt'>[] = []
  let errors = 0
  // Skip header row
  for (let i = 1; i < lines.length; i++) {
    const cols = lines[i].split(',').map((c) => c.trim().replace(/^"|"$/g, ''))
    const [code, category, name, description, supplier, supplierPartNo, supplierItemId, unitPriceStr, unit] = cols
    if (!code || !name || !supplier) { errors++; continue }
    const unitPrice = parseFloat(unitPriceStr)
    if (isNaN(unitPrice)) { errors++; continue }
    parts.push({
      code,
      category: category || 'General',
      name,
      description: description || undefined,
      supplier,
      supplierPartNo: supplierPartNo || '',
      supplierItemId: supplierItemId || undefined,
      unitPrice,
      unit: unit || 'pcs',
      isActive: true,
    })
  }
  return { parts, errors }
}

// Export parts to CSV text
export function exportPartsCsv(parts: Part[]): string {
  const header = 'code,category,name,description,supplier,supplierPartNo,supplierItemId,unitPrice,unit'
  const rows = parts.map((p) =>
    [
      p.code, p.category, `"${p.name}"`,
      `"${p.description ?? ''}"`, `"${p.supplier}"`,
      p.supplierPartNo, p.supplierItemId ?? '',
      p.unitPrice, p.unit,
    ].join(',')
  )
  return [header, ...rows].join('\n')
}

interface PartsStoreState {
  parts: Part[]
  addPart: (data: Omit<Part, 'id' | 'createdAt'>) => Part
  updatePart: (id: string, patch: Partial<Omit<Part, 'id' | 'createdAt'>>) => void
  deletePart: (id: string) => void
  importParts: (incoming: Omit<Part, 'id' | 'createdAt'>[]) => number
}

export const usePartsStore = create<PartsStoreState>()(
  persist(
    (set) => ({
      parts: DEMO_PARTS,

      addPart: (data) => {
        const newPart: Part = {
          ...data,
          id: crypto.randomUUID(),
          createdAt: new Date().toISOString().slice(0, 10),
        }
        set((s) => ({ parts: [...s.parts, newPart] }))
        return newPart
      },

      updatePart: (id, patch) => {
        set((s) => ({
          parts: s.parts.map((p) => (p.id === id ? { ...p, ...patch } : p)),
        }))
      },

      deletePart: (id) => {
        set((s) => ({ parts: s.parts.filter((p) => p.id !== id) }))
      },

      importParts: (incoming) => {
        const now = new Date().toISOString().slice(0, 10)
        const newParts: Part[] = incoming.map((data) => ({
          ...data,
          id: crypto.randomUUID(),
          createdAt: now,
        }))
        set((s) => ({ parts: [...s.parts, ...newParts] }))
        return newParts.length
      },
    }),
    { name: 'epms-parts' }
  )
)
