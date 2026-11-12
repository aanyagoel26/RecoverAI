export function money(minor: number | null | undefined, currency = "USD", compact = false): string {
  const v = (minor ?? 0) / 100;
  return new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: compact && Math.abs(v) >= 1000 ? 1 : 2,
    notation: compact && Math.abs(v) >= 10000 ? "compact" : "standard" }).format(v);
}
export const pct = (x: number | null | undefined, digits = 0) => (x === null || x === undefined ? "—" : `${(x * 100).toFixed(digits)}%`);
export const titleCase = (s: string | null | undefined) =>
  (s ?? "").toLowerCase().split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");

export function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").getTime();
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 0) { const f = -s; return f < 3600 ? `in ${Math.round(f / 60)}m` : f < 86400 ? `in ${Math.round(f / 3600)}h` : `in ${Math.round(f / 86400)}d`; }
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const CATEGORY_LABEL: Record<string, string> = {
  INSUFFICIENT_FUNDS: "Insufficient funds", CARD_DECLINED: "Card declined", AUTHENTICATION_REQUIRED: "Customer action required",
  CHECKOUT_ABANDONED: "Checkout abandoned", APPROVAL_INCOMPLETE: "Approval incomplete", CAPTURE_FAILED: "Capture failed",
  TECHNICAL_ERROR: "Technical error", CONFIGURATION_ISSUE: "Configuration issue", DUPLICATE_ATTEMPT: "Duplicate attempt",
  UNKNOWN: "Unknown", UNCLASSIFIED: "Unclassified",
};
export const ACTION_LABEL: Record<string, string> = {
  SEND_REMINDER: "Send payment reminder", SEND_PAYPAL_INVOICE: "Send PayPal invoice", REQUEST_CUSTOMER_ACTION: "Request customer action", RECOMMEND_ALTERNATIVE_METHOD: "Suggest another payment method",
  GENERATE_CHECKOUT_LINK: "Generate checkout link", NOTIFY_MERCHANT: "Notify merchant", WAIT_FOR_PENDING: "Wait for PayPal",
  RECOMMEND_SUPPORT: "Recommend support", MANUAL_INVESTIGATION: "Manual investigation", CLOSE_CASE: "Close case",
};
export const catLabel = (c?: string | null) => (c ? CATEGORY_LABEL[c] ?? titleCase(c) : "—");
export const actLabel = (a?: string | null) => (a ? ACTION_LABEL[a] ?? titleCase(a) : "—");
