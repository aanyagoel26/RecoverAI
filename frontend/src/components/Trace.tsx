import clsx from "clsx";
import { Check, CircleDashed, Hand, Info, ShieldCheck, X } from "lucide-react";
import { useEffect, useState } from "react";
import { ago } from "../lib/format";
import { Badge, SourceBadge } from "./ui";

const STEP_STYLE: Record<string, [string, JSX.Element]> = {
  ok: ["border-mint-500/50 bg-mint-500/15 text-mint-300", <Check key="c" className="h-3.5 w-3.5" />],
  hold: ["border-amberx-500/50 bg-amberx-500/15 text-amberx-400", <Hand key="h" className="h-3.5 w-3.5" />],
  fail: ["border-rose-500/50 bg-rose-500/15 text-rose-400", <X key="x" className="h-3.5 w-3.5" />],
  info: ["border-volt-500/50 bg-volt-500/15 text-volt-300", <Info key="i" className="h-3.5 w-3.5" />],
  skipped: ["border-ink-600 bg-ink-800 text-ink-400", <CircleDashed key="s" className="h-3.5 w-3.5" />],
};

export const OUTCOME: Record<string, [string, "green" | "amber" | "red" | "blue" | "gray"]> = {
  EXECUTED: ["Executed", "green"], AWAITING_APPROVAL: ["Awaiting your approval", "amber"], BLOCKED: ["Blocked by Trust Firewall", "red"],
  WAITING: ["Waiting for PayPal", "blue"], RECOMMENDED: ["Recommendation only", "blue"], CLOSED: ["Closed — already paid", "gray"],
};

/** Animated pipeline: steps reveal one by one so a viewer can follow the agent's reasoning. */
export function TraceSteps({ run, animate = true }: { run: any; animate?: boolean }) {
  const [shown, setShown] = useState(animate ? 0 : run.steps.length);
  useEffect(() => {
    if (!animate) { setShown(run.steps.length); return; }
    setShown(0);
    const t = setInterval(() => setShown((n: number) => { if (n >= run.steps.length) { clearInterval(t); return n; } return n + 1; }), 320);
    return () => clearInterval(t);
  }, [run.id, animate, run.steps.length]);
  return (
    <ol className="relative">
      {run.steps.map((s: any, i: number) => {
        const [cls, icon] = STEP_STYLE[s.status] ?? STEP_STYLE.info;
        const visible = i < shown;
        return (
          <li key={s.key} className={clsx("relative flex gap-3 pb-4 transition-all duration-300", visible ? "opacity-100" : "translate-y-1 opacity-0")}>
            {i < run.steps.length - 1 && <span className="absolute left-[13px] top-7 h-[calc(100%-20px)] w-px bg-ink-700" />}
            <span className={clsx("z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border", cls)}>{icon}</span>
            <div className="min-w-0 pt-0.5">
              <div className="flex items-center gap-2"><span className="num text-[10px] text-ink-500">{String(i + 1).padStart(2, "0")}</span>
                <span className="text-sm font-semibold text-white">{s.label}</span></div>
              <p className="mt-0.5 text-xs leading-relaxed text-ink-300">{s.detail}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function Firewall({ checks }: { checks: any[] }) {
  const passed = checks.filter((c) => c.status === "pass").length;
  const scored = checks.filter((c) => c.status !== "info").length;
  return (
    <div className="rounded-xl border border-ink-700 bg-ink-850">
      <div className="flex items-center justify-between border-b border-ink-700 px-4 py-2.5">
        <span className="flex items-center gap-2 text-sm font-semibold text-white"><ShieldCheck className="h-4 w-4 text-mint-400" /> Trust Firewall</span>
        <span className="num text-xs text-ink-300">{passed}/{scored} passed</span>
      </div>
      <ul className="divide-y divide-ink-800">
        {checks.map((c) => (
          <li key={c.key} className="flex items-start gap-2.5 px-4 py-2">
            <span className={clsx("mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full",
              c.status === "pass" ? "bg-mint-500/20 text-mint-300" : c.status === "hold" ? "bg-amberx-500/20 text-amberx-400" : c.status === "info" ? "bg-volt-500/20 text-volt-300" : "bg-rose-500/20 text-rose-400")}>
              {c.status === "pass" ? <Check className="h-3 w-3" /> : c.status === "hold" ? <Hand className="h-2.5 w-2.5" /> : c.status === "info" ? <Info className="h-3 w-3" /> : <X className="h-3 w-3" />}
            </span>
            <div className="min-w-0"><div className="text-xs font-semibold text-ink-100">{c.label}</div><div className="text-[11px] text-ink-400">{c.detail}</div></div>
          </li>))}
      </ul>
    </div>
  );
}

const RAIL_COLOR: Record<string, string> = { PAY_LATER: "from-[#003087] to-[#0070E0]", VENMO: "from-[#008CFF] to-[#3D95CE]",
  PAYPAL_INVOICE: "from-[#1A2847] to-[#3B7BFF]", PAYPAL_CHECKOUT: "from-[#003087] to-[#009CDE]" };

export function RailCard({ rail }: { rail: any }) {
  if (!rail) return null;
  return (
    <div className={clsx("rounded-xl bg-gradient-to-br p-[1px]", RAIL_COLOR[rail.key] ?? RAIL_COLOR.PAYPAL_CHECKOUT)}>
      <div className="rounded-[11px] bg-ink-900/90 p-4">
        <div className="label text-volt-300">PayPal recovery rail</div>
        <div className="mt-1 text-lg font-bold text-white">{rail.label}</div>
        <p className="mt-0.5 text-[11px] text-ink-400">{rail.product}</p>
        <p className="mt-2 text-xs leading-relaxed text-ink-200">{rail.reason}</p>
      </div>
    </div>
  );
}

export function RunHeader({ run }: { run: any }) {
  const [label, tone] = OUTCOME[run.outcome] ?? [run.outcome, "gray"];
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge tone={tone}>{label}</Badge><SourceBadge s={run.data_source} />
      <span className="text-[11px] text-ink-500">{ago(run.created_at)}</span>
    </div>
  );
}
