import type { RouteDef } from '@uniops/shell'
import AccountsPayablePage from '@/pages/finance/AccountsPayablePage'
import ApReconciliationPage from '@/pages/finance/ApReconciliationPage'
import ApLedgerHealthPage from '@/pages/finance/ApLedgerHealthPage'
import ApDuplicateInvoicesPage from '@/pages/finance/ApDuplicateInvoicesPage'
import ApCashFlowPage from '@/pages/finance/ApCashFlowPage'
import AccountsReceivablePage from '@/pages/finance/AccountsReceivablePage'
import GeneralLedgerPage from '@/pages/finance/GeneralLedgerPage'
import JournalVouchersPage from '@/pages/finance/JournalVouchersPage'
import AccountBalancePage from '@/pages/finance/AccountBalancePage'
import BudgetActualPage from '@/pages/finance/BudgetActualPage'
import JvValidationPage from '@/pages/finance/JvValidationPage'
import PaymentsPage from '@/pages/finance/PaymentsPage'
import PaymentBatchPage from '@/pages/finance/PaymentBatchPage'
import BankReconciliationPage from '@/pages/finance/BankReconciliationPage'
import BankStatementCsvPage from '@/pages/finance/BankStatementCsvPage'
import CoaConfigPage from '@/pages/finance/CoaConfigPage'
import TaxSettingsPage from '@/pages/finance/TaxSettingsPage'
import BankSettingsPage from '@/pages/finance/BankSettingsPage'
import QboMirrorPage from '@/pages/finance/QboMirrorPage'
import QboCreditImportPage from '@/pages/finance/QboCreditImportPage'
import BudgetConfigPage from '@/pages/budget/BudgetConfigPage'
import BudgetDashboardPage from '@/pages/budget/BudgetDashboardPage'
import BudgetPlansPage from '@/pages/budget/BudgetPlansPage'
import BudgetPlanDetailPage from '@/pages/budget/BudgetPlanDetailPage'
import BudgetCatalogPage from '@/pages/budget/BudgetCatalogPage'
import FactorLibraryPage from '@/pages/budget/FactorLibraryPage'
import CostAnomaliesPage from '@/pages/finance/CostAnomaliesPage'
import MaterialTracePage from '@/pages/finance/MaterialTracePage'
import GrossMarginPage from '@/pages/finance/GrossMarginPage'
import CostChainPage from '@/pages/finance/CostChainPage'
import BatchCostPage from '@/pages/finance/BatchCostPage'
import StandardCostPage from '@/pages/finance/StandardCostPage'

export const financeRoutes: RouteDef[] = [
  { path: '/finance/ap', element: <AccountsPayablePage />, tab: { title: 'Accounts Payable', icon: 'CreditCard', keyStrategy: 'static', pinned: true } },
  { path: '/finance/ap-recon', element: <ApReconciliationPage />, tab: { title: 'AP Reconciliation', icon: 'ClipboardCheck', keyStrategy: 'static' } },
  { path: '/finance/ap-ledger-health', element: <ApLedgerHealthPage />, tab: { title: 'AP Subledger Health', icon: 'HeartPulse', keyStrategy: 'static' } },
  { path: '/finance/ap-duplicate-invoices', element: <ApDuplicateInvoicesPage />, tab: { title: 'AP Duplicate Invoices', icon: 'CopyX', keyStrategy: 'static' } },
  { path: '/finance/ap-cash-flow', element: <ApCashFlowPage />, tab: { title: 'AP Cash Flow', icon: 'TrendingUp', keyStrategy: 'static' } },
  { path: '/finance/ar', element: <AccountsReceivablePage />, tab: { title: 'Accounts Receivable', icon: 'Receipt', keyStrategy: 'static' } },
  { path: '/finance/gl', element: <GeneralLedgerPage />, tab: { title: 'General Ledger', icon: 'BookOpen', keyStrategy: 'static' } },
  { path: '/finance/journal-vouchers', element: <JournalVouchersPage />, tab: { title: 'Journal Vouchers', icon: 'FileText', keyStrategy: 'static' } },
  { path: '/finance/account-balance', element: <AccountBalancePage />, tab: { title: 'Account Balance', icon: 'Scale', keyStrategy: 'static' } },
  { path: '/finance/budget-actual', element: <BudgetActualPage />, tab: { title: 'Budget Actual', icon: 'Target', keyStrategy: 'static' } },
  { path: '/finance/jv-validation', element: <JvValidationPage />, tab: { title: 'JV Validation', icon: 'ShieldAlert', keyStrategy: 'static' } },
  { path: '/finance/payments', element: <PaymentsPage />, tab: { title: 'Payments', icon: 'Wallet', keyStrategy: 'static' } },
  { path: '/finance/payment-batches', element: <PaymentBatchPage />, tab: { title: 'Payment Batches', icon: 'Banknote', keyStrategy: 'static' } },
  { path: '/finance/bank', element: <BankReconciliationPage />, tab: { title: 'Bank Reconciliation', icon: 'Landmark', keyStrategy: 'static' } },
  // The CSV importer the v2 workbench replaced. Not in the sidebar — reachable
  // from the workbench, because a bank whose PDF we cannot read yet still has
  // to be importable.
  { path: '/finance/bank/csv-import', element: <BankStatementCsvPage />, tab: { title: 'Statement Import (CSV)', icon: 'Landmark', keyStrategy: 'static' } },
  { path: '/finance/qbo', element: <QboMirrorPage />, tab: { title: 'QuickBooks', icon: 'RefreshCw', keyStrategy: 'static' } },
  // Throwaway: delete with QboCreditImportPage when QuickBooks is retired.
  { path: '/finance/qbo-credit-import', element: <QboCreditImportPage />, tab: { title: 'QBO Credit Import', icon: 'Download', keyStrategy: 'static' } },

  { path: '/budget/dashboard', element: <BudgetDashboardPage />, tab: { title: 'Budget Dashboard', icon: 'LayoutDashboard', keyStrategy: 'static' } },
  { path: '/budget/plans', element: <BudgetPlansPage />, tab: { title: 'Budget Plans', icon: 'ClipboardList', keyStrategy: 'static' } },
  { path: '/budget/plans/:id', element: <BudgetPlanDetailPage />, tab: { title: 'Budget Plan', icon: 'ClipboardList', keyStrategy: 'param', paramName: 'id' } },
  { path: '/budget/catalog', element: <BudgetCatalogPage />, tab: { title: 'Account Catalog', icon: 'FolderTree', keyStrategy: 'static' } },
  { path: '/budget/factors', element: <FactorLibraryPage />, tab: { title: 'Factor Library', icon: 'FlaskConical', keyStrategy: 'static' } },
  { path: '/budget/config', element: <BudgetConfigPage />, tab: { title: 'Budget Config', icon: 'SlidersHorizontal', keyStrategy: 'static' } },

  { path: '/finance/coa', element: <CoaConfigPage />, tab: { title: 'Chart of Accounts', icon: 'FolderTree', keyStrategy: 'static' } },
  { path: '/finance/tax', element: <TaxSettingsPage />, tab: { title: 'Tax Settings', icon: 'Percent', keyStrategy: 'static' } },
  { path: '/finance/bank-settings', element: <BankSettingsPage />, tab: { title: 'Bank Settings', icon: 'Settings', keyStrategy: 'static' } },

  // AI Financial Analysis (finance.analysis.view — not view_finance).
  { path: '/finance/analysis/anomalies', element: <CostAnomaliesPage />, tab: { title: 'Cost Anomalies', icon: 'Activity', keyStrategy: 'static' } },
  // The picker (sidebar entry) and one tab PER MATERIAL. A single static tab
  // was re-focused with its old query when a second Trace link was clicked —
  // the shell's openTab focuses an existing key without touching its path.
  { path: '/finance/analysis/trace', element: <MaterialTracePage />, tab: { title: 'Material Cost Trace', icon: 'GitBranch', keyStrategy: 'static' } },
  { path: '/finance/analysis/margin', element: <GrossMarginPage />, tab: { title: 'Gross Margin', icon: 'Percent', keyStrategy: 'static' } },
  { path: '/finance/analysis/chain', element: <CostChainPage />, tab: { title: 'Cost Chain', icon: 'Network', keyStrategy: 'static' } },
  { path: '/finance/analysis/chain/:material', element: <CostChainPage />, tab: { title: (p) => `Chain ${p.material ?? ''}`, icon: 'Network', keyStrategy: 'param', paramName: 'material' } },
  { path: '/finance/analysis/batches', element: <BatchCostPage />, tab: { title: 'Batch Cost', icon: 'Boxes', keyStrategy: 'static' } },
  { path: '/finance/analysis/batches/:material', element: <BatchCostPage />, tab: { title: (p) => `Batches ${p.material ?? ''}`, icon: 'Boxes', keyStrategy: 'param', paramName: 'material' } },
  { path: '/finance/analysis/standard', element: <StandardCostPage />, tab: { title: 'Standard Cost', icon: 'Ruler', keyStrategy: 'static' } },
  { path: '/finance/analysis/standard/:material', element: <StandardCostPage />, tab: { title: (p) => `Standard ${p.material ?? ''}`, icon: 'Ruler', keyStrategy: 'param', paramName: 'material' } },
  { path: '/finance/analysis/trace/:material', element: <MaterialTracePage />, tab: { title: (p) => `Trace ${p.material ?? ''}`, icon: 'GitBranch', keyStrategy: 'param', paramName: 'material' } },
]
