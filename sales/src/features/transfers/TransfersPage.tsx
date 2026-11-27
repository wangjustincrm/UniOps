import { TransferList } from './TransferList'

/** Transfers (FR-OB-80 … 84): Draft → Ready to send → the 943 → the 997 → the
 *  944, its lines allocated and summed per lot. */
export default function TransfersPage() {
  return <TransferList help="page:transfers" title="Transfers" />
}
