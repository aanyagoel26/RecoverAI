import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, ArrowUpRight, Bot, CircleDollarSign, Gauge, Lightbulb, ShieldAlert, TrendingUp } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, Category, Empty, Loading, PageHeader, PaymentStatus, Prob, SourceBadge, Stat } from "../components/ui";
import { api } from "../lib/api";
import { isLight } from "../lib/theme";
import { ago, money, pct, titleCase } from "../lib/format";

const DARK = { grid: "#1A2847", axis: "#6B7FA6", blue: "#3B7BFF", green: "#34D399", amber: "#FBBF24", red: "#FB7185", gray: "#3A4D75", violet: "#A78BFA", cursor: "#111D37" };
const LIGHT = { grid: "#E2E8F0", axis: "#64748B", blue: "#2563EB", green: "#059669", amber: "#D97706", red: "#E11D48", gray: "#94A3B8", violet: "#7C3AED", cursor: "#F1F5F9" };
/** Read at render time so charts follow the current theme (the page remounts on theme change). */
export const CHART = new Proxy(DARK, { get: (_t, k: string) => (isLight() ? LIGHT : DARK)[k as keyof typeof DARK] }) as typeof DARK;
export const tooltipStyle = {
  get contentStyle() { return isLight() ? { background: "#FFFFFF", border: "1px solid #E2E8F0", borderRadius: 8, fontSize: 12, color: "#0F172A" } : { background: "#0D172D", border: "1px solid #26375C", borderRadius: 8, fontSize: 12 }; },
  get labelStyle() { return { color: isLight() ? "#334155" : "#C9D3E6" }; },
};
const STATUS_COLOR: Record<string, string> = { COMPLETED: CHART.green, DECLINED: CHART.red, FAILED: CHART.red, PENDING: CHART.amber, CREATED: CHART.gray, APPROVED: CHART.blue, REFUNDED: CHART.violet };

export function recoveredSub(o: any): string {
  const src = o.recovered_by_source ?? {};
  const real = (src.PAYPAL_SANDBOX ?? 0) + (src.PAYPAL_LIVE ?? 0), demo = src.DEMO_SIMULATED ?? 0;
  const parts = [real && `${real} PayPal-confirmed`, demo && `${demo} demo-simulated`].filter(Boolean);
  return `${o.recovered_count} recoveries${parts.length ? " · " + parts.join(", ") : ""}`;
}

export function ActivityFeed({ limit = 12 }: { limit?: number }) {
  const q = useQuery({ queryKey: ["agent-activity", limit], queryFn: () => api(`/api/agent/activity?limit=${limit}`), refetchInterval: 6000 });
  if (q.isLoading) return <Loading />;
  const items = q.data?.items ?? [];
  if (!items.length) return <Empty title="No agent activity yet" />;
  return (
    <ol className="divide-y divide-ink-800">
      {items.map((a: any) => (
        <li key={a.id} className="flex gap-3 px-5 py-3">
          <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${a.actor_type === "paypal" ? "bg-volt-400" : a.action.includes("recovered") ? "bg-mint-400" : a.action.includes("blocked") || a.action.includes("duplicate") ? "bg-amberx-400" : "bg-ink-500"}`} />
          <div className="min-w-0 flex-1">
            <p className="text-[13px] leading-snug text-ink-200">{a.summary}</p>
            <p className="mt-0.5 text-[11px] text-ink-500">{titleCase(a.actor_type)} · {ago(a.created_at)}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function Dashboard() {
  const nav = useNavigate();
  const ov = useQuery({ queryKey: ["overview", 30], queryFn: () => api("/api/analytics/overview?days=30"), refetchInterval: 8000 });
  const rev = useQuery({ queryKey: ["revenue", 30], queryFn: () => api("/api/analytics/revenue?days=30"), refetchInterval: 15000 });
  const br = useQuery({ queryKey: ["breakdowns", 30], queryFn: () => api("/api/analytics/breakdowns?days=30"), refetchInterval: 15000 });
  const cases = useQuery({ queryKey: ["cases", "active", "priority"], queryFn: () => api("/api/recovery/cases?status=active&sort=priority"), refetchInterval: 8000 });
  const ins = useQuery({ queryKey: ["insights"], queryFn: () => api("/api/insights"), refetchInterval: 30000 });
  const recent = useQuery({ queryKey: ["payments", "recent"], queryFn: () => api("/api/payments?limit=8"), refetchInterval: 5000 });

  if (ov.isLoading) return <Loading label="Loading dashboard" />;
  const o = ov.data;
  const cur = o?.currency ?? "USD";
  const series = (rev.data?.series ?? []).map((d: any) => ({ ...d, day: d.day.slice(5), recovered: d.recovered_minor / 100, at_risk: d.at_risk_opened_minor / 100, volume: d.volume_minor / 100 }));
  const topCases = (cases.data?.items ?? []).slice(0, 5);
  const recs = (cases.data?.items ?? []).filter((c: any) => c.awaiting_approval).slice(0, 3);

  return (
    <div>
      <PageHeader title="Overview" sub="Last 30 days · updates live"
        actions={<><span className="inline-flex items-center gap-2 text-xs text-ink-400"><span className="live-dot h-2 w-2 rounded-full bg-mint-400" /> Live monitoring</span>
          <Link to="/recovery" className="btn-primary btn-sm">Open Recovery Centre <ArrowUpRight className="h-3.5 w-3.5" /></Link></>} />

      {o.total_attempts === 0 && (
        <div className="card mb-4 flex flex-wrap items-center justify-between gap-4 border-volt-500/30 bg-volt-500/5 p-5">
          <div><div className="font-semibold text-white">No payments yet</div>
            <p className="mt-1 text-sm text-ink-300">Connect PayPal Sandbox, add your webhook, then run a test checkout. RecoverAI starts monitoring as soon as the first payment arrives.</p></div>
          <div className="flex gap-2"><Link to="/integrations" className="btn-primary btn-sm">Connect PayPal</Link><Link to="/payments" className="btn-ghost btn-sm">Create test checkout</Link></div>
        </div>)}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Total processed" value={money(o.revenue_processed_minor, cur)} sub={`${o.successful} completed of ${o.total_attempts} attempts`} icon={<CircleDollarSign className="h-4 w-4" />} />
        <Stat label="Revenue at risk" tone="amber" value={money(o.revenue_at_risk_minor, cur)} sub={`${o.open_cases} open recovery cases`} icon={<ShieldAlert className="h-4 w-4" />} />
        <Stat label="Revenue recovered" tone="green" value={money(o.revenue_recovered_minor, cur)} sub={recoveredSub(o)} icon={<TrendingUp className="h-4 w-4" />} />
        <Stat label="Recovery success rate" tone="blue" value={pct(o.recovery_conversion_rate)} sub={`Payment success rate ${pct(o.success_rate)}`} icon={<Gauge className="h-4 w-4" />} />
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {[["Failed", o.failed, "text-rose-400", "/payments?filter=failed"], ["Pending", o.pending, "text-amberx-400", "/payments?filter=pending"],
          ["Not approved", o.approval_incomplete, "text-ink-200", "/payments?filter=incomplete"], ["Abandoned checkouts", o.abandoned_sessions, "text-amberx-400", "/payments?filter=abandoned"],
          ["Need customer action", o.requires_customer_action, "text-volt-300", "/recovery"], ["Avg. time to recover", o.avg_hours_to_recovery !== null ? `${o.avg_hours_to_recovery}h` : "—", "text-mint-300", "/analytics"]]
          .map(([l, v, c, to]: any) => (
            <button key={l} onClick={() => nav(to)} className="card px-4 py-3 text-left transition hover:border-ink-500">
              <div className="text-[11px] text-ink-400">{l}</div><div className={`num mt-1 text-lg font-semibold ${c}`}>{v}</div>
            </button>))}
      </div>

      {!!ins.data?.items?.length && (
        <Card className="mt-4" title={<span className="flex items-center gap-2"><Lightbulb className="h-4 w-4 text-amberx-400" /> What needs attention</span>}
          sub="Computed from your payment records — each insight shows its evidence" action={<Link to="/intelligence" className="btn-ghost btn-sm">Failure Intelligence <ArrowUpRight className="h-3.5 w-3.5" /></Link>}>
          <div className="grid gap-px bg-ink-800 md:grid-cols-2">{ins.data.items.map((i: any, n: number) => (
            <div key={n} className="bg-ink-900 px-5 py-3.5">
              <div className="flex items-start gap-2"><span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${i.severity === "high" ? "bg-rose-400" : i.severity === "medium" ? "bg-amberx-400" : "bg-volt-400"}`} />
                <div><div className="text-sm font-semibold text-white">{i.title}</div><div className="mt-0.5 text-[11px] text-ink-400">{i.evidence}</div>
                  <div className="mt-1 text-xs text-ink-200">→ {i.recommendation}</div></div></div>
            </div>))}</div>
        </Card>)}

      <div className="mt-6 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Revenue recovery" sub="New revenue at risk vs. PayPal-confirmed recovered revenue, per day">
          <div className="h-72 px-2 py-4">
            <ResponsiveContainer>
              <AreaChart data={series} margin={{ left: 8, right: 16 }}>
                <defs>
                  <linearGradient id="gr" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={CHART.green} stopOpacity={0.4} /><stop offset="100%" stopColor={CHART.green} stopOpacity={0} /></linearGradient>
                  <linearGradient id="ga" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={CHART.amber} stopOpacity={0.25} /><stop offset="100%" stopColor={CHART.amber} stopOpacity={0} /></linearGradient>
                </defs>
                <CartesianGrid stroke={CHART.grid} vertical={false} />
                <XAxis dataKey="day" stroke={CHART.axis} fontSize={11} tickLine={false} axisLine={false} minTickGap={24} />
                <YAxis stroke={CHART.axis} fontSize={11} tickLine={false} axisLine={false} tickFormatter={(v) => `$${v >= 1000 ? (v / 1000).toFixed(1) + "k" : v}`} />
                <Tooltip {...tooltipStyle} formatter={(v: number, n: string) => [money(v * 100, cur), n === "recovered" ? "Recovered" : "At risk (new)"]} />
                <Area type="monotone" dataKey="at_risk" stroke={CHART.amber} fill="url(#ga)" strokeWidth={1.5} />
                <Area type="monotone" dataKey="recovered" stroke={CHART.green} fill="url(#gr)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Payment status" sub="Attempts in the last 30 days">
          <div className="h-72 px-2 py-4">
            <ResponsiveContainer>
              <BarChart data={br.data?.payment_status ?? []} layout="vertical" margin={{ left: 16, right: 16 }}>
                <CartesianGrid stroke={CHART.grid} horizontal={false} />
                <XAxis type="number" stroke={CHART.axis} fontSize={11} tickLine={false} axisLine={false} allowDecimals={false} />
                <YAxis type="category" dataKey="status" stroke={CHART.axis} fontSize={11} tickLine={false} axisLine={false} width={84} tickFormatter={(s) => (s === "CREATED" ? "Not approved" : titleCase(s))} />
                <Tooltip {...tooltipStyle} cursor={{ fill: CHART.cursor }} />
                <Bar dataKey="count" radius={[0, 4, 4, 0]}>{(br.data?.payment_status ?? []).map((d: any) => <Cell key={d.status} fill={STATUS_COLOR[d.status] ?? CHART.gray} />)}</Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title={<span className="flex items-center gap-2"><Bot className="h-4 w-4 text-volt-400" /> AI recovery recommendations</span>} sub="Prepared by the agent, waiting for your approval"
          action={<Link to="/agent" className="text-xs font-semibold text-volt-400 hover:text-volt-300">Agent centre →</Link>}>
          {recs.length === 0 ? <Empty title="Nothing waiting for approval" body="New recommendations appear here as the agent analyses payments." /> : (
            <div className="grid gap-3 p-4 md:grid-cols-3">
              {recs.map((c: any) => (
                <button key={c.id} onClick={() => nav(`/recovery?case=${c.id}`)} className="rounded-lg border border-ink-700 bg-ink-850 p-4 text-left transition hover:border-volt-500/60">
                  <div className="flex items-center justify-between"><span className="text-sm font-semibold text-white">{c.customer.name}</span><span className="num text-sm text-white">{money(c.amount_minor, c.currency)}</span></div>
                  <div className="mt-2"><Category c={c.failure_category} /></div>
                  <div className="mt-3"><Prob p={c.recovery_probability} w="w-24" /></div>
                  <p className="mt-3 line-clamp-2 text-xs text-ink-400">{c.priority_reason}</p>
                  <div className="mt-3 text-xs font-semibold text-volt-300">Review & approve →</div>
                </button>))}
            </div>)}
        </Card>
        <Card title={<span className="flex items-center gap-2"><Activity className="h-4 w-4 text-mint-400" /> AI activity</span>}>
          <div className="max-h-[340px] overflow-y-auto"><ActivityFeed limit={10} /></div>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card title="Recent transactions" action={<Link to="/payments" className="text-xs font-semibold text-volt-400 hover:text-volt-300">All payments →</Link>}>
          <table className="w-full text-sm">
            <tbody className="divide-y divide-ink-800">
              {(recent.data?.items ?? []).map((p: any) => (
                <tr key={p.id} className="row-hover cursor-pointer" onClick={() => nav(`/payments/${p.id}`)}>
                  <td className="px-5 py-2.5"><div className="font-mono text-xs text-white">{p.reference}</div><div className="text-[11px] text-ink-400">{p.customer?.name}</div></td>
                  <td className="px-2"><PaymentStatus s={p.status} /></td>
                  <td className="hidden px-2 sm:table-cell"><SourceBadge s={p.data_source} /></td>
                  <td className="num px-5 text-right text-white">{money(p.amount_minor, p.currency)}</td>
                </tr>))}
            </tbody>
          </table>
        </Card>
        <Card title={<span className="flex items-center gap-2"><AlertTriangle className="h-4 w-4 text-amberx-400" /> High-priority recovery cases</span>}
          action={<Link to="/recovery" className="text-xs font-semibold text-volt-400 hover:text-volt-300">Priority queue →</Link>}>
          {topCases.length === 0 ? <Empty title="No open cases" /> : (
            <table className="w-full text-sm">
              <tbody className="divide-y divide-ink-800">
                {topCases.map((c: any) => (
                  <tr key={c.id} className="row-hover cursor-pointer" onClick={() => nav(`/recovery?case=${c.id}`)}>
                    <td className="num w-8 pl-5 text-xs text-ink-400">#{c.rank}</td>
                    <td className="px-3 py-2.5"><div className="text-white">{c.customer.name}</div><div className="text-[11px] text-ink-400">{c.order_number} · {titleCase(c.case_type)}</div></td>
                    <td className="hidden px-2 md:table-cell"><Category c={c.failure_category ?? (c.case_type === "PENDING_PAYMENT" ? null : c.case_type)} /></td>
                    <td className="px-2"><Prob p={c.recovery_probability} /></td>
                    <td className="num px-5 text-right text-white">{money(c.amount_minor, c.currency)}</td>
                  </tr>))}
              </tbody>
            </table>)}
        </Card>
      </div>
    </div>
  );
}
