import { AllCommunityModule, ModuleRegistry, themeQuartz, type ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { actLabel, ago, money, titleCase } from "../lib/format";
import { isLight } from "../lib/theme";
import { Badge, Category, PaymentStatus, Prob, Risk, SourceBadge } from "./ui";

ModuleRegistry.registerModules([AllCommunityModule]);

const darkTheme = themeQuartz.withParams({
  backgroundColor: "#0A1224", foregroundColor: "#E8EDF6", headerBackgroundColor: "#0D172D", headerTextColor: "#9AAACB",
  borderColor: "#1A2847", chromeBackgroundColor: "#0D172D", accentColor: "#3B7BFF", rowHoverColor: "#111D37",
  oddRowBackgroundColor: "#0A1224", fontFamily: "inherit", fontSize: 13, headerFontSize: 11, headerFontWeight: 700,
  spacing: 7, wrapperBorderRadius: 12, browserColorScheme: "dark",
});
const lightTheme = themeQuartz.withParams({
  backgroundColor: "#FFFFFF", foregroundColor: "#0F172A", headerBackgroundColor: "#F8FAFC", headerTextColor: "#475569",
  borderColor: "#E2E8F0", chromeBackgroundColor: "#F8FAFC", accentColor: "#2563EB", rowHoverColor: "#F1F5F9",
  fontFamily: "inherit", fontSize: 13, headerFontSize: 11, headerFontWeight: 700, spacing: 7, wrapperBorderRadius: 12, browserColorScheme: "light",
});

/** Live payments table on AG Grid: sorting, column filters, resizing, pagination and CSV export. */
export function PaymentsGrid({ rows, onReady }: { rows: any[]; onReady?: (api: any) => void }) {
  const nav = useNavigate();
  const cols = useMemo<ColDef[]>(() => [
    { headerName: "Transaction", field: "reference", pinned: "left", width: 170, filter: "agTextColumnFilter",
      cellRenderer: (p: any) => <span className="flex items-center gap-1.5"><span className="font-mono text-xs text-white">{p.value}</span><SourceBadge s={p.data.data_source} /></span> },
    { headerName: "Customer", valueGetter: (p: any) => p.data.customer?.name, filter: "agTextColumnFilter", width: 160 },
    { headerName: "Amount", field: "amount_minor", width: 120, filter: "agNumberColumnFilter", type: "rightAligned",
      valueFormatter: (p: any) => money(p.value, p.data.currency), cellClass: "num" },
    { headerName: "Currency", field: "currency", width: 95 },
    { headerName: "Method", field: "payment_method", width: 135, valueFormatter: (p: any) => ({ paypal: "PayPal", paypal_invoice: "PayPal Invoice" } as Record<string, string>)[p.value] ?? titleCase(p.value) },
    { headerName: "Status", field: "status", width: 150, filter: true,
      cellRenderer: (p: any) => <span className="flex items-center gap-1"><PaymentStatus s={p.value} />{p.data.is_recovery_attempt && p.value === "COMPLETED" && <Badge tone="green">Recovered</Badge>}</span> },
    { headerName: "Failure reason", field: "failure_category", width: 190, filter: true,
      cellRenderer: (p: any) => p.data.status === "PENDING" ? <span className="text-xs text-amberx-400">{titleCase(p.data.paypal_issue) || "Pending"}</span> : <Category c={p.value} /> },
    { headerName: "Risk", field: "risk_level", width: 100, cellRenderer: (p: any) => <Risk level={p.data.status === "COMPLETED" ? null : p.value} /> },
    { headerName: "Recovery", field: "recovery_probability", width: 130, filter: "agNumberColumnFilter",
      cellRenderer: (p: any) => p.data.status === "COMPLETED" ? <span className="text-ink-500">—</span> : <Prob p={p.value} /> },
    { headerName: "Recommended action", field: "recommended_action", width: 210, valueFormatter: (p: any) => p.data.status === "COMPLETED" ? "—" : actLabel(p.value) },
    { headerName: "Updated", field: "updated_at", width: 110, valueFormatter: (p: any) => ago(p.value), sort: undefined },
  ], []);
  return (
    <div className="h-[640px]">
      <AgGridReact theme={isLight() ? lightTheme : darkTheme} rowData={rows} columnDefs={cols} getRowId={(p) => p.data.id} animateRows
        defaultColDef={{ sortable: true, resizable: true }} pagination paginationPageSize={20} paginationPageSizeSelector={[20, 50, 100]}
        onRowClicked={(e) => nav(`/payments/${e.data.id}`)} rowStyle={{ cursor: "pointer" }} onGridReady={(e) => onReady?.(e.api)}
        overlayNoRowsTemplate="No payments match" />
    </div>
  );
}
