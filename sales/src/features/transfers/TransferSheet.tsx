import { factTone } from '@/features/common/statusTones'
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ActionButton, ConfirmAction, actionDone, type ActionResultRow, DateCell, DetailList, LocalTable, ViewFileButton, RecordSheet, Section, StatusCell, queryKeys, useFileViewer, type ColumnDef, type RecordTab, compareRecord, useActionSends, useRefresh, columnsOf, useRecord, Text, Cluster } from '@/platform-ui'
import { Attachments } from '@/features/common/Attachments'
import { RecordActivity } from '@/features/common/RecordActivity'
import { RecordFilesTable } from '@/features/common/RecordFilesTable'
import { cancelTransfer, fetchTransfer, readyTransfer, releaseTransfer, resendTransfer, transfersClient, transferPdf, type TransferDetail, type TransferLineRead, type TransferRead } from './transfersApi'
import { AMENDABLE, CANCELLABLE, MANAGE, errorMessages } from './transferRules'
import { AmendForm, ResolveForm, TransferForm } from './TransferForms'
import { ReceiptRows, Received, type PackedRow } from './TransferReceived'

const colTransferLineRead = columnsOf<TransferLineRead>()
const lineColumns: ColumnDef<TransferLineRead, unknown>[] = [
  colTransferLineRead.text('line_no', '#'),
  colTransferLineRead.text('item_code', 'Item'),
  colTransferLineRead.text('qty_ea', 'Requested EA'),
  colTransferLineRead.text('received_ea', 'Received EA'),
  colTransferLineRead.status('line_status', 'Line'),
]

export function TransferSheet({ row }: { row: TransferRead }) {
  const id = row.id
  const qc = useQueryClient()
  const { view, viewer } = useFileViewer()
  const q = useRecord(transfersClient, id, fetchTransfer, { errors: 'inline' })
  const sheetRefresh = useRefresh(q, { queryKey: queryKeys.detail(transfersClient, id), compare: compareRecord, noChange: 'Up to date', auto: 'follow' })
  const [cancelling, setCancelling] = useState(false)
  const [releasing, setReleasing] = useState(false)
  const [editing, setEditing] = useState(false)
  const [amending, setAmending] = useState(false)
  const [settling, setSettling] = useState(false)
  const done = async (d: TransferDetail) => {
    qc.setQueryData(queryKeys.detail(transfersClient, id), d)
    await qc.invalidateQueries({ queryKey: queryKeys.list(transfersClient) })
  }
  const t = q.data
  const reread = [queryKeys.detail(transfersClient, id), queryKeys.list(transfersClient)]
  const readySends = useActionSends(transfersClient, 'ready')
  const resendSends = useActionSends(transfersClient, 'resend')
  const sent = t ? t.lines.reduce((n, l) => n + l.qty_ea, 0) : 0
  const received = t ? t.lines.reduce((n, l) => n + l.received_ea, 0) : 0
  const discrepant = !!t?.receipts.some((r) => r.discrepancies && r.discrepancies.length > 0)
  // What the applied 944s received, row by row, with the file it came in (user 2026-11-13: under each line, as an order's).
  const receivedRows: PackedRow[] = (t?.receipts ?? []).filter((r) => r.applied).flatMap((r) => r.rows.map((x) => ({ ...x, file: r.document_name ?? `#${r.document_id}`, receipt_date: r.receipt_date })))
  const packedFor = (lineNo: number) => receivedRows.filter((x) => x.transfer_line_no === lineNo)
  const offTransfer = receivedRows.filter((x) => x.transfer_line_no == null)
  const tabs: RecordTab[] = !t ? [] : editing ? [{
    id: 'edit', label: 'Edit draft', render: () => (
      <TransferForm t={t} onCancel={() => setEditing(false)} onSaved={async (d) => { setEditing(false); await done(d) }} />),
  }] : amending ? [{
    id: 'amend', label: 'Amend and send again', render: () => (
      <AmendForm t={t} onCancel={() => setAmending(false)} onSaved={async (d) => { setAmending(false); await done(d) }} />),
  }] : settling ? [{
    id: 'settle', label: 'Settle the difference', render: () => (
      <Section title="Settle the receipt difference">
        {t.receipts.flatMap((r) => r.discrepancies ?? []).length > 0 && (
          <Text as="ul" tone="danger" size="xs" className="mb-3">{t.receipts.flatMap((r) => r.discrepancies ?? []).map((x) => <li key={x}>{x}</li>)}</Text>)}
        <ResolveForm t={t} onCancel={() => setSettling(false)} onSaved={async (d) => { setSettling(false); await done(d) }} />
      </Section>),
  }] : [
    { id: 'overview', label: 'Overview', tone: t.error ? 'danger' : undefined, render: () => (
      <Section title="Transfer">
        <DetailList items={[
          ['From → to', `${t.from_warehouse} → ${t.to_warehouse}`],
          ['943', t.control ? `${t.control} · file ${t.file_status} · 997 ${t.ack_status}` : null], ['Notes', t.notes], ['Problem', t.error],
          ['Difference settled', t.resolution ? `${t.resolution === 'corrected' ? 'Corrected to what arrived' : 'Accepted as it is'}: ${t.resolution_reason ?? ''} (${t.resolved_by_name ?? ''})` : null],
        ]} />
      </Section>
    ) },
    { id: 'lines', label: 'Lines', count: t.lines.length, tone: offTransfer.length ? 'warning' : undefined, render: () => (
      <>
        <Text as="p" tone="muted" size="xs" className="mb-2">▸ shows what the warehouse's 944 received for a line: pallet, lot and quantity.</Text>
        <LocalTable<TransferLineRead> ariaLabel="Transfer lines" columns={lineColumns} rows={t.lines} rowKey={(l) => l.id}
          expand={(l) => { const rows = packedFor(l.line_no); return rows.length ? <Received rows={rows} /> : null }}
          footer={[null, 'Total', sent, received, null]} />
        {offTransfer.length > 0 && (
          <Section title="Received but not on the transfer" plain>
            <Received rows={offTransfer} label="Received, not on the transfer" />
          </Section>
        )}
      </>
    ) },
    { id: 'receipts', label: 'Receipts (944)', count: t.receipts.length, tone: discrepant ? 'warning' : undefined,
      empty: t.receipts.length === 0 && 'No 944 yet', render: () => t.receipts.map((r) => (
        <Section key={r.id} title={<>944 {r.document_name} · <DateCell value={r.receipt_date} /> · {r.applied || r.shadow ? r.outcome : 'not applied (second 944)'}</>}>
          {r.discrepancies && r.discrepancies.length > 0 && <Text as="ul" tone="danger" size="xs">{r.discrepancies.map((x) => <li key={x}>{x}</li>)}</Text>}
          <ReceiptRows rows={r.rows} label={`944 ${r.document_name ?? r.id} lines`} />
        </Section>
      )) },
    { id: 'documents', label: 'Documents', count: t.documents.length,
      tone: t.documents.some((x) => x.direction === 'OUT' && ['Failed', 'Suppressed'].includes(x.file_status ?? '')) ? 'danger' : undefined,
      render: () => (
        <>
          <Section title="Printable">
            <Cluster gap={2} align="stretch">
              <ViewFileButton source={transferPdf(t.id, t.transfer_number)}>Transfer order (PDF)</ViewFileButton>
            </Cluster>
          </Section>
          <Attachments resource={transfersClient} type="transfer" id={t.id} />
          <Section title="Files received and sent">
            <RecordFilesTable documents={t.documents} empty="No file yet: the 943 goes when it is ready to send." />
          </Section>
        </>
      ) },
    { id: 'activity', label: 'Activity', count: t.timeline.length, empty: t.timeline.length === 0 && 'Nothing recorded yet',
      render: () => <RecordActivity entries={t.timeline} /> },
  ]
  return (
    <>
      {viewer}
      <RecordSheet
        title={row.transfer_number}
        subtitle={`${row.from_warehouse ?? '?'} → ${row.to_warehouse ?? '?'}`}
        badges={<StatusCell value={t?.status ?? row.status} />}
        loading={q.isLoading} error={q.error} errorMessages={errorMessages}
        refresh={sheetRefresh}
        facts={t ? [
          { label: 'Status', value: t.status, tone: factTone(t.status) },
          { label: 'Sent EA', value: sent },
          { label: 'Received EA', value: received, tone: t.receipts.length > 0 && received < sent ? 'warning' : 'neutral' },
          { label: '943', value: t.control ?? '—', hint: t.file_status ?? undefined },
          { label: '997', value: t.ack_status ?? '—' },
          ...(t.receipt_date ? [{ label: 'Received on', value: <DateCell value={t.receipt_date} /> }] : []),
        ] : undefined}
        actions={t && !editing ? (
          <>
            {t.status === 'Draft' && <ActionButton size="sm" permission={MANAGE} errorMessages={errorMessages} done="Ready to send: the 943 goes on the next pass" sends={readySends} invalidates={reread} onAction={() => readyTransfer(id, t.version)}>Ready to send</ActionButton>}
            {t.status === 'Draft' && <ActionButton size="sm" variant="secondary" permission={MANAGE} onAction={() => setEditing(true)}>Edit draft</ActionButton>}
            {t.status === 'Failed' && <ActionButton size="sm" permission={MANAGE} errorMessages={errorMessages} done={(r) => actionDone(r as ActionResultRow, '943 queued again')} sends={resendSends} invalidates={reread} onAction={() => resendTransfer(id, t.version)}>Resend 943</ActionButton>}
            {t.status === 'On Hold' && <ActionButton size="sm" permission={MANAGE} onAction={() => setReleasing(true)}>Release hold…</ActionButton>}
            {AMENDABLE.includes(t.status) && t.receipts.every((r) => !r.applied) && !amending && !settling && (
              <ActionButton size="sm" variant="secondary" permission={MANAGE} onAction={() => setAmending(true)}>Amend and send again…</ActionButton>)}
            {['Discrepancy', 'Partially Received'].includes(t.status) && !settling && (
              <ActionButton size="sm" permission={MANAGE} onAction={() => setSettling(true)}>Settle the difference…</ActionButton>)}
          </>
        ) : undefined}
        more={t && !editing ? [
          { label: 'Transfer order (PDF)', onSelect: () => view(transferPdf(t.id, t.transfer_number)), opensDialog: true },
          ...(CANCELLABLE.includes(t.status)
            ? [{ label: 'Cancel transfer', permission: MANAGE, onSelect: () => setCancelling(true), opensDialog: true }] : []),
        ] : undefined}
        tabs={tabs}
      />
      {t && (
        <ConfirmAction open={cancelling} onOpenChange={setCancelling} title={`Cancel transfer ${t.transfer_number}?`} destructive
          confirmLabel="Cancel transfer" reason={{ label: 'Why' }} errorMessages={errorMessages}
          description="Allowed before its 943 is sent, or after it was rejected. A queued 943 is withdrawn."
          record={t} invalidates={reread} onConfirm={(why) => cancelTransfer(id, t.version, why)} />
      )}
      {t && (
        <ConfirmAction open={releasing} onOpenChange={setReleasing} title={`Release transfer ${t.transfer_number}?`}
          confirmLabel={`Back to ${t.status_before_hold ?? 'its status'}`} reason={{ label: 'What you decided (compared both 944s)' }} errorMessages={errorMessages}
          description="A second, different 944 put it On Hold. The first 944 stays applied; the second stays kept, not applied."
          record={t} invalidates={reread} onConfirm={(why) => releaseTransfer(id, t.version, why)} />
      )}
    </>
  )
}
