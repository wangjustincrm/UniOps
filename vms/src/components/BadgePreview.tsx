/** Printable visitor badge layout (PRD §2.3 VMS-LB-001..006).
 *
 * Config-driven: all text, field visibility, and styling come from
 * BadgeConfig (admin-editable). The print-CSS skeleton (mm sizing,
 * print-color-adjust, page-break rules) is preserved from the original
 * hard-coded layout so physical badges still cut and print correctly.
 * This component is the single renderer shared by the print page and the
 * admin live preview.
 */
import { QRCodeSVG } from 'qrcode.react'
import type {
  AccessArea, BadgeConfig, BadgeMetaKey, Visit, Visitor, UserBrief,
} from '@/services/api'
import { BADGE_CONFIG_DEFAULTS } from '@/services/api'
import { formatDate } from '@/lib/utils'

const PURPOSE_LABEL: Record<string, string> = {
  meeting: 'Meeting', maintenance: 'Maintenance', tour: 'Tour', audit: 'Audit',
  interview: 'Interview', delivery: 'Delivery', other: 'Other',
}

/** Resolve a whitelisted meta key to its display string, or null to omit. */
function metaValue(
  key: BadgeMetaKey, visit: Visit, host: UserBrief | null,
): string | null {
  switch (key) {
    case 'visit_date':
      return formatDate(visit.visit_date)
    case 'host':
      return host?.full_name ?? null
    case 'valid_until':
      return visit.planned_departure
        ? new Date(visit.planned_departure).toLocaleTimeString('en-CA',
            { hour: '2-digit', minute: '2-digit', hour12: false })
        : null
    case 'vehicle_plate':
      return visit.vehicle_plate || null
    case 'accompanying_count':
      return visit.accompanying_count != null ? String(visit.accompanying_count) : null
    case 'visit_purpose':
      return PURPOSE_LABEL[visit.visit_purpose] ?? visit.visit_purpose
    default:
      return null
  }
}

export function BadgePreview({
  visit,
  visitor,
  host,
  config,
}: {
  visit: Visit
  visitor: Visitor
  host: UserBrief | null
  config?: BadgeConfig
}) {
  const cfg = config ?? BADGE_CONFIG_DEFAULTS
  const area = cfg.band.areas[visit.access_area as AccessArea] ?? cfg.band.areas.office
  const { style } = cfg

  const name = cfg.identity.name_uppercase
    ? `${visitor.first_name} ${visitor.last_name}`.toUpperCase()
    : `${visitor.first_name} ${visitor.last_name}`

  const rows = cfg.meta_fields
    .filter((f) => f.visible)
    .map((f) => ({ label: f.label, value: metaValue(f.key, visit, host) }))
    .filter((r) => r.value != null)

  return (
    <div className="badge-card">
      {/* Top color band */}
      <div className="badge-band" style={{ background: area.bg, color: area.fg }}>
        <div className="badge-band-title">{cfg.band.title}</div>
        {cfg.band.show_zone && <div className="badge-band-zone">{area.label}</div>}
        {cfg.band.show_risk && (
          <div className="badge-band-risk">{`${area.risk} ${cfg.band.risk_suffix}`.trim()}</div>
        )}
      </div>

      {/* Body */}
      <div className="badge-body">
        <div className="badge-info">
          <p className="badge-name">{name}</p>
          {cfg.identity.show_company && visitor.company_name && <p className="badge-company">{visitor.company_name}</p>}

          {rows.length > 0 && (
            <dl className="badge-meta">
              {rows.map((r, i) => (
                <div key={i}><dt>{r.label}</dt><dd>{r.value}</dd></div>
              ))}
            </dl>
          )}
        </div>

        {cfg.qr.show && (
          <div className="badge-qr">
            <QRCodeSVG value={visit.id} size={120} includeMargin={false} />
            {cfg.qr.label && <p className="badge-qr-label">{cfg.qr.label}</p>}
          </div>
        )}
      </div>

      {/* Footer */}
      {cfg.footer.show && cfg.footer.lines.length > 0 && (
        <div className="badge-footer">
          {cfg.footer.lines.map((line, i) => <p key={i}>{line}</p>)}
        </div>
      )}

      {/* ── Styles ──────────────────────────────────────────────────────── */}
      <style>{`
        .badge-card {
          width: var(--badge-width, 140mm);
          min-height: 100mm;
          background: white;
          border: 1px solid #D9DFE3;
          border-radius: 4px;
          box-shadow: 0 1px 3px rgba(0,0,0,0.08);
          overflow: hidden;
          font-family: 'Inter', system-ui, sans-serif;
          page-break-inside: avoid;
          color: ${style.text_color};
          -webkit-print-color-adjust: exact;
          print-color-adjust: exact;
        }
        .badge-card * {
          -webkit-print-color-adjust: exact;
          print-color-adjust: exact;
        }

        .badge-band {
          padding: 8mm 10mm 6mm;
          display: flex;
          align-items: baseline;
          justify-content: space-between;
          gap: 4mm;
          line-height: 1;
        }
        .badge-band-zone  { font-size: ${style.band_title_size_pt}pt; font-weight: 600; letter-spacing: 1px; }
        .badge-band-title { font-size: 12pt; font-weight: 700; letter-spacing: 2px; }
        .badge-band-risk  { font-size: 9pt;  font-weight: 700; letter-spacing: 1.5px; }

        .badge-body {
          display: flex;
          gap: 6mm;
          padding: 6mm 10mm;
          align-items: flex-start;
        }
        .badge-info  { flex: 1; min-width: 0; }
        .badge-name {
          font-size: ${style.name_size_pt}pt;
          font-weight: 800;
          margin: 0;
          line-height: 1.1;
          letter-spacing: 0.5px;
          word-break: break-word;
          text-align: ${style.name_align};
        }
        .badge-company {
          font-size: 11pt;
          font-weight: 500;
          margin: 1mm 0 4mm;
          color: ${style.company_color};
        }
        .badge-meta {
          margin: 0;
          font-size: 9pt;
          color: #3A4D5C;
          display: grid;
          gap: 1.2mm;
        }
        .badge-meta > div { display: flex; gap: 2mm; }
        .badge-meta dt {
          width: 24mm;
          font-weight: 600;
          color: #667685;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          font-size: 8pt;
          margin: 0;
        }
        .badge-meta dd { font-weight: 500; margin: 0; }

        .badge-qr { width: 34mm; text-align: center; flex-shrink: 0; }
        .badge-qr-label {
          margin: 1mm 0 0;
          font-size: 7pt;
          color: #667685;
          letter-spacing: 0.5px;
          text-transform: uppercase;
        }

        .badge-footer {
          background: #F7F8F9;
          border-top: 1px solid #ECEEF0;
          padding: 3mm 10mm;
          font-size: 8pt;
          color: ${style.footer_color};
          line-height: 1.4;
        }
        .badge-footer p { margin: 0; }

        @media print {
          /* Card-local print tweaks only. Page-level rules (@page orientation,
             page breaks, .no-print) live in BadgePrintPage so the 2-up
             (two copies per row) layout controls them. */
          .badge-card { box-shadow: none !important; border: 1px dashed #999; margin: 0; }
        }
      `}</style>
    </div>
  )
}
