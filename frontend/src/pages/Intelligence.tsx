import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Lightbulb, Sparkles } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Card, Category, Empty, ErrorBox, Loading, Notice, PageHeader, Spinner, Stat, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { ago, catLabel, money, pct, titleCase } from "../lib/format";
import { CHART, tooltipStyle } from "./Dashboard";

const GROUP_LABEL: Record<string, string> = {
  authentication: "Authentication / approval", customer: "Customer-side", technical: "Technical", configuration: "Merchant configuration", duplicate: "Duplicate", unknown: "Unknown",
};
const groupColor = (g: string) => ({ authentication: CHART.blue, customer: CHART.amber, technical: CHART.red, configuration: CHART.violet, duplicate: CHART.gray, unknown: CHART.axis } as Record<string, string>)[g];
const SEV: Record<string, "red" | "amber" | "blue"> = { high: "red", medium: "amber", info: "blue" };

export function InsightList({ items, compact }: { items: any[]; compact?: boolean }) {
  return (
    <ul className="divide-y divide-ink-800">{items.map((i, n) => (
      <li key={n} className={compact ? "px-5 py-3" : "px-5 py-4"}>
        <div className="flex items-start gap-3">
          <Lightbulb className={`mt-0.5 h-4 w-4 shrink-0 ${i.severity === "high" ? "text-rose-400" : i.severity === "medium" ? "text-amberx-400" : "text-volt-400"}`} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold text-white">{i.title}</span><Badge tone={SEV[i.severity] ?? "gray"}>{i.severity}</Badge></div>
            <div className="mt-0.5 text-xs text-ink-400">Evidence: {i.evidence}</div>
            <div className="mt-1 text-xs text-ink-200">{i.recommendation}</div>
          </div>
        </div>
      </li>))}
    </ul>);
}

export function Intelligence() {
  const [days, setDays] = useState("30");
  const [group, setGroup] = useState("");
  const [category, setCategory] = useState("");
  const [source, setSource] = useState("");
  const qs = new URLSearchParams({ days, ...(group && { group }), ...(category && { category }), ...(source && { source }) }).toString();
  const q = useQuery({ queryKey: ["intelligence", qs], queryFn: () => api(`/api/intelligence?${qs}`), refetchInterval: 20000 });
  const ai = useMutation({ mutationFn: () => api(`/api/intelligence/ai-summary?days=${days}`, { method: "POST" }) });
  const r = q.data;
  return (
    <div>
      <PageHeader title="Failure Intelligence" sub="Why payments fail, where the patterns are, and which recovery approach works — computed from your payment records." />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Tabs value={days} onChange={setDays} items={[{ value: "7", label: "7 days" }, { value: "30", label: "30 days" }, { value: "90", label: "90 days" }, { value: "0", label: "All time" }]} />
        <select className="input w-auto py-1.5 text-xs" value={group} onChange={(e) => { setGroup(e.target.value); setCategory(""); }} aria-label="Failure group">
          <option value="">All groups</option>{Object.entries(GROUP_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <select className="input w-auto py-1.5 text-xs" value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Failure category">
          <option value="">All categories</option>{(r?.categories ?? []).map((c: any) => <option key={c.category} value={c.category}>{c.label}</option>)}
          {category && !(r?.categories ?? []).some((c: any) => c.category === category) && <option value={category}>{catLabel(category)}</option>}</select>
        <select className="input w-auto py-1.5 text-xs" value={source} onChange={(e) => setSource(e.target.value)} aria-label="Data source">
          <option value="">All sources</option><option value="paypal_sandbox">PayPal Sandbox</option><option value="paypal_live">PayPal Live</option><option value="demo">Demo</option></select>
      </div>
      {q.isLoading || !r ? <Loading /> : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Stat label="Payment attempts" value={r.totals.attempts} />
            <Stat label="Failed attempts" value={r.totals.failed} tone="red" sub={`${pct(r.totals.failure_rate, 1)} failure rate`} />
            <Stat label="Amount affected" value={money(r.totals.failed_minor)} tone="amber" />
            <Stat label="Abandoned checkouts" value={r.totals.abandoned_sessions} sub="Tracked separately from failures" />
            <Stat label="Uncertain outcome" value={r.totals.uncertain} tone={r.totals.uncertain ? "red" : undefined} sub="Needs reconciliation" />
          </div>

          <div className="grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2" title="Failures over time" sub="By failure group">
              <div className="h-64 p-4">{!r.trend.length ? <Empty title="No failures in this period" /> : (
                <ResponsiveContainer><BarChart data={r.trend}>
                  <CartesianGrid stroke={CHART.grid} vertical={false} /><XAxis dataKey="day" stroke={CHART.axis} fontSize={11} tickFormatter={(d) => d.slice(5)} /><YAxis stroke={CHART.axis} fontSize={11} allowDecimals={false} />
                  <Tooltip {...tooltipStyle} cursor={{ fill: CHART.cursor }} /><Legend wrapperStyle={{ fontSize: 11 }} />
                  {Object.keys(GROUP_LABEL).map((g) => <Bar key={g} dataKey={g} name={GROUP_LABEL[g]} stackId="a" fill={groupColor(g)} />)}
                </BarChart></ResponsiveContainer>)}</div>
            </Card>
            <Card title="Failure groups">
              <ul className="divide-y divide-ink-800">{r.groups.map((g: any) => (
                <li key={g.group}><button className={`row-hover flex w-full items-center justify-between px-5 py-2.5 text-left text-sm ${group === g.group ? "bg-volt-500/10" : ""}`} onClick={() => { setGroup(group === g.group ? "" : g.group); setCategory(""); }}>
                  <span className="flex items-center gap-2 text-ink-200"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: groupColor(g.group) }} />{GROUP_LABEL[g.group]}</span>
                  <span className="num text-white">{g.count}</span></button></li>))}</ul>
            </Card>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <Card title="Insights" sub="Computed from your records — each one shows its evidence"
              action={<button className="btn-ghost btn-sm" onClick={() => ai.mutate()} disabled={ai.isPending}>{ai.isPending ? <Spinner /> : <Sparkles className="h-3.5 w-3.5 text-violet-400" />} AI summary</button>}>
              {ai.data && (ai.data.available ? (
                <div className="border-b border-ink-800 bg-violet-500/5 px-5 py-4">
                  <div className="label mb-1.5 flex items-center gap-1.5"><Sparkles className="h-3 w-3" /> AI summary · {ai.data.model}</div>
                  <ul className="list-disc space-y-1 pl-5 text-sm text-ink-200">{ai.data.bullets.map((b: string, i: number) => <li key={i}>{b}</li>)}</ul>
                  {ai.data.next_step && <p className="mt-2 text-xs text-volt-300">Next step: {ai.data.next_step}</p>}
                </div>) : (
                <div className="border-b border-ink-800 px-5 py-3"><Notice tone="amber">AI summary unavailable: {ai.data.reason}. The insights below are computed without AI and remain accurate. <Link to="/integrations" className="underline">Connect an AI provider</Link></Notice></div>))}
              <ErrorBox error={ai.error} />
              {r.insights.length ? <InsightList items={r.insights} /> : <Empty title="Nothing notable yet" />}
            </Card>
            <Card title="Which recovery works" sub="Outcome of closed cases, by failure category">
              {!r.outcomes.length ? <Empty title="No recovery cases in this period" /> : (
                <div className="overflow-x-auto"><table className="w-full min-w-[420px] text-xs">
                  <thead><tr className="border-b border-ink-700 text-left">{["Category", "Cases", "Won", "Lost", "Open", "Win rate"].map((h) => <th key={h} className="label px-3 py-2.5">{h}</th>)}</tr></thead>
                  <tbody className="divide-y divide-ink-800">{r.outcomes.map((o: any) => (
                    <tr key={o.category} title={o.recovered_minor ? `${money(o.recovered_minor)} recovered` : undefined}><td className="px-3 py-2.5"><Category c={o.category} /></td><td className="num px-3 text-ink-200">{o.cases}</td><td className="num px-3 text-mint-300">{o.recovered}</td>
                      <td className="num px-3 text-rose-300">{o.unrecovered}</td><td className="num px-3 text-ink-300">{o.open}</td><td className="num px-3 text-white">{o.rate == null ? "—" : pct(o.rate)}</td></tr>))}</tbody></table></div>)}
            </Card>
          </div>

          <div className="grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2" title="Failure patterns" sub="Grouped by PayPal reason code">
              {!r.patterns.length ? <Empty title="No patterns" /> : (
                <div className="overflow-x-auto"><table className="w-full min-w-[560px] text-xs">
                  <thead><tr className="border-b border-ink-700 text-left">{["Reason code", "Category", "Count", "Amount", "Last seen", "Example"].map((h) => <th key={h} className="label px-3 py-2.5">{h}</th>)}</tr></thead>
                  <tbody className="divide-y divide-ink-800">{r.patterns.map((p: any) => (
                    <tr key={p.code} title={`Sources: ${p.sources.map(titleCase).join(", ")}`}><td className="max-w-[220px] truncate px-3 py-2.5 font-mono text-ink-100">{p.code}</td><td className="px-3"><Category c={p.category} /></td><td className="num px-3 text-white">{p.count}</td>
                      <td className="num px-3 text-ink-200">{money(p.amount_minor)}</td><td className="whitespace-nowrap px-3 text-ink-400">{ago(p.last_seen)}</td>
                      <td className="px-3"><Link to={`/payments/${p.example_id}`} className="font-mono text-volt-400 hover:underline">{p.example}</Link></td></tr>))}</tbody></table></div>)}
            </Card>
            <Card title="Repeat failures" sub={`${r.repeat_orders} order(s) failed more than once`}>
              {!r.repeat_customers.length ? <Empty icon={<AlertTriangle className="h-5 w-5" />} title="No repeat customers" body="No customer failed twice in this period." /> : (
                <ul className="divide-y divide-ink-800">{r.repeat_customers.map((c: any) => (
                  <li key={c.customer_id} className="flex items-center justify-between px-5 py-2.5 text-sm"><Link to={`/customers?open=${c.customer_id}`} className="text-ink-100 hover:text-volt-300">{c.customer}</Link><Badge tone="red">{c.failures} failures</Badge></li>))}</ul>)}
            </Card>
          </div>
        </div>)}
    </div>);
}
