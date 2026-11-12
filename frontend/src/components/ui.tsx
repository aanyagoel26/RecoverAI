import clsx from "clsx";
import { AlertTriangle, Inbox, Loader2, X } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { catLabel, titleCase } from "../lib/format";

export function Card({ children, className, title, action, sub }: { children: ReactNode; className?: string; title?: ReactNode; action?: ReactNode; sub?: ReactNode }) {
  return (
    <section className={clsx("card", className)}>
      {(title || action) && (
        <header className="flex items-start justify-between gap-3 border-b border-ink-700/60 px-5 py-3.5">
          <div>
            <h3 className="text-sm font-semibold text-white">{title}</h3>
            {sub && <p className="mt-0.5 text-xs text-ink-400">{sub}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

const TONES: Record<string, string> = {
  green: "bg-mint-500/12 text-mint-300 ring-mint-500/25",
  amber: "bg-amberx-500/12 text-amberx-400 ring-amberx-500/25",
  red: "bg-rose-500/12 text-rose-400 ring-rose-500/25",
  blue: "bg-volt-500/12 text-volt-300 ring-volt-500/25",
  gray: "bg-ink-700/60 text-ink-200 ring-ink-500/30",
  violet: "bg-violet-500/12 text-violet-300 ring-violet-500/25",
};
export function Badge({ tone = "gray", children, className, title }: { tone?: keyof typeof TONES; children: ReactNode; className?: string; title?: string }) {
  return <span title={title} className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset", TONES[tone], className)}>{children}</span>;
}

const PAY_TONE: Record<string, keyof typeof TONES> = { COMPLETED: "green", PENDING: "amber", DECLINED: "red", FAILED: "red", CREATED: "gray", APPROVED: "blue", REFUNDED: "violet", VOIDED: "gray", AUTHORIZED: "blue", CANCELLED: "gray", EXPIRED: "gray", PARTIALLY_REFUNDED: "violet" };
export const PaymentStatus = ({ s }: { s: string }) => <Badge tone={PAY_TONE[s] ?? "gray"}>{s === "CREATED" ? "Not approved" : titleCase(s)}</Badge>;

const CASE_TONE: Record<string, keyof typeof TONES> = { OPEN: "blue", AWAITING_APPROVAL: "amber", IN_PROGRESS: "blue", RECOVERED: "green", CLOSED_ALREADY_PAID: "gray", CLOSED_UNRECOVERED: "red" };
export const CaseStatus = ({ s }: { s: string }) => <Badge tone={CASE_TONE[s] ?? "gray"}>{s === "CLOSED_ALREADY_PAID" ? "Closed · paid" : titleCase(s)}</Badge>;

export function SourceBadge({ s }: { s: string }) {
  if (s === "DEMO_SIMULATED") return <Badge tone="violet" title="Simulated demo data — not a real PayPal transaction">DEMO</Badge>;
  if (s === "PAYPAL_SANDBOX") return <Badge tone="blue" title="Real PayPal Sandbox transaction">SANDBOX</Badge>;
  return <Badge tone="green" title="Live PayPal transaction">LIVE</Badge>;
}
export function Category({ c }: { c?: string | null }) {
  if (!c) return <span className="text-ink-400">—</span>;
  const tone = c === "UNKNOWN" ? "gray" : c === "DUPLICATE_ATTEMPT" ? "violet" : c === "CONFIGURATION_ISSUE" || c === "CAPTURE_FAILED" ? "red" : c === "CHECKOUT_ABANDONED" ? "amber" : "blue";
  return <Badge tone={tone as any}>{catLabel(c)}</Badge>;
}
export function Risk({ level }: { level?: string | null }) {
  if (!level) return <span className="text-ink-400">—</span>;
  return <Badge tone={level === "LOW" ? "green" : level === "MEDIUM" ? "amber" : "red"}>{titleCase(level)}</Badge>;
}

export function Prob({ p, w = "w-16" }: { p?: number | null; w?: string }) {
  if (p === null || p === undefined) return <span className="text-ink-400">—</span>;
  const col = p >= 0.6 ? "bg-mint-400" : p >= 0.35 ? "bg-amberx-400" : "bg-rose-400";
  return (
    <span className="inline-flex items-center gap-2">
      <span className={clsx("h-1.5 overflow-hidden rounded-full bg-ink-700", w)}><span className={clsx("block h-full rounded-full", col)} style={{ width: `${Math.round(p * 100)}%` }} /></span>
      <span className="num text-xs text-ink-200">{Math.round(p * 100)}%</span>
    </span>
  );
}

export function Stat({ label, value, sub, tone, icon }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "green" | "red" | "amber" | "blue"; icon?: ReactNode }) {
  const col = tone === "green" ? "text-mint-300" : tone === "red" ? "text-rose-400" : tone === "amber" ? "text-amberx-400" : tone === "blue" ? "text-volt-300" : "text-white";
  return (
    <div className="card card-pad fade-up">
      <div className="flex items-center justify-between"><span className="label">{label}</span><span className="text-ink-400">{icon}</span></div>
      <div className={clsx("num mt-2 text-2xl font-semibold", col)}>{value}</div>
      {sub && <div className="mt-1 text-xs text-ink-400">{sub}</div>}
    </div>
  );
}

export const Spinner = ({ className }: { className?: string }) => <Loader2 className={clsx("animate-spin", className ?? "h-4 w-4")} />;
export function Loading({ label = "Loading" }: { label?: string }) {
  return <div className="flex items-center justify-center gap-2 py-16 text-sm text-ink-400"><Spinner /> {label}…</div>;
}
export function Empty({ title, body, action, icon }: { title: string; body?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      <div className="mb-3 rounded-xl bg-ink-800 p-3 text-ink-400">{icon ?? <Inbox className="h-5 w-5" />}</div>
      <p className="text-sm font-semibold text-white">{title}</p>
      {body && <p className="mt-1 max-w-sm text-xs text-ink-400">{body}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="flex items-start gap-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-300"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{(error as Error).message ?? String(error)}</div>;
}
export function Notice({ children, tone = "blue" }: { children: ReactNode; tone?: "blue" | "amber" | "violet" | "green" }) {
  const c = { blue: "border-volt-500/30 bg-volt-500/8 text-volt-300", amber: "border-amberx-500/30 bg-amberx-500/10 text-amberx-400", violet: "border-violet-500/30 bg-violet-500/10 text-violet-300", green: "border-mint-500/30 bg-mint-500/10 text-mint-300" }[tone];
  return <div className={clsx("rounded-lg border px-3 py-2 text-xs leading-relaxed", c)}>{children}</div>;
}

export function Drawer({ open, onClose, title, children, width = "max-w-2xl" }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; width?: string }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (open) window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-950/70 backdrop-blur-sm" onClick={onClose} />
      <aside className={clsx("relative flex h-full w-full flex-col border-l border-ink-700 bg-ink-900 shadow-2xl fade-up", width)} role="dialog" aria-modal>
        <header className="flex items-center justify-between border-b border-ink-700 px-5 py-4">
          <div className="text-sm font-semibold text-white">{title}</div>
          <button className="rounded-md p-1 text-ink-400 hover:bg-ink-800 hover:text-white" onClick={onClose} aria-label="Close"><X className="h-4 w-4" /></button>
        </header>
        <div className="flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { value: T; label: ReactNode; count?: number }[] }) {
  return (
    <div className="flex flex-wrap gap-1 rounded-lg border border-ink-700 bg-ink-900 p-1">
      {items.map((i) => (
        <button key={i.value} className={clsx("tab", value === i.value && "tab-active")} onClick={() => onChange(i.value)}>
          {i.label}{i.count !== undefined && <span className="num ml-1.5 text-ink-400">{i.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-white sm:text-2xl">{title}</h1>
        {sub && <p className="mt-1 text-sm text-ink-300">{sub}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)}
      className={clsx("relative h-5 w-9 rounded-full transition", checked ? "bg-mint-500" : "bg-ink-600")}>
      <span className={clsx("absolute top-0.5 h-4 w-4 rounded-full bg-white transition", checked ? "left-[18px]" : "left-0.5")} />
    </button>
  );
}

/** Accessible confirmation dialog for destructive or money-affecting actions. */
export function Confirm({ open, title, body, confirmLabel = "Confirm", danger, busy, onConfirm, onCancel }: {
  open: boolean; title: string; body: ReactNode; confirmLabel?: string; danger?: boolean; busy?: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    if (open) window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onCancel]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" role="alertdialog" aria-modal aria-labelledby="confirm-title">
      <div className="absolute inset-0 bg-ink-950/70 backdrop-blur-sm" onClick={onCancel} />
      <div className="card relative w-full max-w-md p-5 fade-up">
        <h2 id="confirm-title" className="text-base font-semibold text-white">{title}</h2>
        <div className="mt-2 text-sm text-ink-300">{body}</div>
        <div className="mt-5 flex justify-end gap-2">
          <button className="btn-ghost" onClick={onCancel} autoFocus>Cancel</button>
          <button className={danger ? "btn-danger" : "btn-primary"} onClick={onConfirm} disabled={busy}>{busy && <Spinner />} {confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
