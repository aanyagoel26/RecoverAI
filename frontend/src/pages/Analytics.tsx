import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, Empty, Loading, PageHeader, Stat, Tabs } from "../components/ui";
import { api, downloadCsv } from "../lib/api";
import { catLabel, money, pct, titleCase } from "../lib/format";
import { CHART, recoveredSub, tooltipStyle } from "./Dashboard";

const PALETTE = [CHART.blue, CHART.red, CHART.amber, CHART.violet, CHART.green, "#38BDF8", "#F472B6", CHART.gray, "#A3E635", "#FB923C"];

export function Analytics() {
  const [range, setRange] = useState("30");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const qs = range === "custom" ? (start ? `start=${start}${end ? `&end=${end}` : ""}` : "days=30") : `days=${range}`;
  const ov = useQuery({ queryKey: ["overview", qs], queryFn: () => api(`/api/analytics/overview?${qs}`), refetchInterval: 15000 });
  const rev = useQuery({ queryKey: ["revenue", qs], queryFn: () => api(`/api/analytics/revenue?${qs}`) });
  const br = useQuery({ queryKey: ["breakdowns", qs], queryFn: () => api(`/api/analytics/breakdowns?${qs}`) });
  const series = (rev.data?.series ?? []).map((d: any) => ({ ...d, day: d.day.slice(5), volume: d.volume_minor / 100, recovered: d.recovered_minor / 100, at_risk: d.at_risk_opened_minor / 100 }));
  const o = ov.data;
  const axis = { stroke: CHART.axis, fontSize: 11, tickLine: false, axisLine: false } as const;
  const usd = (v: number) => `$${v >= 1000 ? (v / 1000).toFixed(1) + "k" : v}`;

  return (
    <div>
      <PageHeader title="Recovery analytics" sub="Every chart is computed from payment and case records. DEMO rows are simulated."
        actions={<>
          <Tabs value={range} onChange={setRange} items={[{ value: "0", label: "Today" }, { value: "7", label: "7d" }, { value: "30", label: "30d" }, { value: "90", label: "90d" }, { value: "custom", label: "Custom" }]} />
          {range === "custom" && <><input type="date" className="input w-36 py-1.5" value={start} onChange={(e) => setStart(e.target.value)} aria-label="Start date" /><input type="date" className="input w-36 py-1.5" value={end} onChange={(e) => setEnd(e.target.value)} aria-label="End date" /></>}
        </>} />
      <div className="mb-4 flex flex-wrap gap-2">
        {(["payments", "cases", "daily"] as const).map((k) => <button key={k} className="btn-ghost btn-sm" onClick={() => downloadCsv(`/api/analytics/export.csv?kind=${k}&${qs}`, `recoverai-${k}.csv`)}><Download className="h-3.5 w-3.5" /> Export {k} CSV</button>)}
      </div>
      {!o ? <Loading /> : (<>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Stat label="Payment success rate" value={pct(o.success_rate, 1)} sub={`${o.successful} completed · ${o.failed} failed`} tone="blue" />
          <Stat label="Recovery success rate" value={pct(o.recovery_conversion_rate, 1)} sub={o.definitions.recovery_conversion_rate} tone="green" />
          <Stat label="Actual revenue recovered" value={money(o.revenue_recovered_minor)} sub={recoveredSub(o)} tone="green" />
          <Stat label="Avg. time to recovery" value={o.avg_hours_to_recovery != null ? `${o.avg_hours_to_recovery}h` : "—"} sub={`At risk now: ${money(o.revenue_at_risk_minor)}`} tone="amber" />
        </div>
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <Card title="Daily payment volume" sub="Completed payment value per day">
            <div className="h-64 p-3"><ResponsiveContainer><BarChart data={series}><CartesianGrid stroke={CHART.grid} vertical={false} /><XAxis dataKey="day" {...axis} minTickGap={20} /><YAxis {...axis} tickFormatter={usd} />
              <Tooltip {...tooltipStyle} cursor={{ fill: CHART.cursor }} formatter={(v: number) => money(v * 100)} /><Bar dataKey="volume" name="Volume" fill={CHART.blue} radius={[3, 3, 0, 0]} /></BarChart></ResponsiveContainer></div>
          </Card>
          <Card title="Successful vs failed payments" sub="Attempts per day">
            <div className="h-64 p-3"><ResponsiveContainer><BarChart data={series}><CartesianGrid stroke={CHART.grid} vertical={false} /><XAxis dataKey="day" {...axis} minTickGap={20} /><YAxis {...axis} allowDecimals={false} />
              <Tooltip {...tooltipStyle} cursor={{ fill: CHART.cursor }} /><Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="completed" name="Completed" stackId="a" fill={CHART.green} /><Bar dataKey="failed" name="Failed" stackId="a" fill={CHART.red} radius={[3, 3, 0, 0]} /></BarChart></ResponsiveContainer></div>
          </Card>
          <Card title="Revenue at risk vs. actual recovered" sub="Per day">
            <div className="h-64 p-3"><ResponsiveContainer><LineChart data={series}><CartesianGrid stroke={CHART.grid} vertical={false} /><XAxis dataKey="day" {...axis} minTickGap={20} /><YAxis {...axis} tickFormatter={usd} />
              <Tooltip {...tooltipStyle} formatter={(v: number) => money(v * 100)} /><Legend wrapperStyle={{ fontSize: 11 }} />
              <Line dataKey="at_risk" name="New at risk" stroke={CHART.amber} dot={false} strokeWidth={2} /><Line dataKey="recovered" name="Recovered" stroke={CHART.green} dot={false} strokeWidth={2} /></LineChart></ResponsiveContainer></div>
          </Card>
          <Card title="Payment failure categories" sub="Classified from PayPal issue codes">
            {!br.data?.failure_categories?.length ? <Empty title="No failures in range" /> : (
              <div className="h-64 p-3"><ResponsiveContainer><BarChart data={br.data.failure_categories} layout="vertical" margin={{ left: 40 }}><CartesianGrid stroke={CHART.grid} horizontal={false} />
                <XAxis type="number" {...axis} allowDecimals={false} /><YAxis type="category" dataKey="category" {...axis} width={150} tickFormatter={catLabel} />
                <Tooltip {...tooltipStyle} cursor={{ fill: CHART.cursor }} labelFormatter={catLabel} /><Bar dataKey="count" radius={[0, 4, 4, 0]}>{br.data.failure_categories.map((_: any, i: number) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}</Bar></BarChart></ResponsiveContainer></div>)}
          </Card>
        </div>
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <Card title="Recovery performance by strategy" sub="Cases where the strategy was executed">
            {!br.data?.strategies?.length ? <Empty title="No cases in range" /> : (
              <table className="w-full text-sm"><thead><tr className="border-b border-ink-700 text-left">{["Strategy", "Cases", "Recovered", "Rate", "Revenue"].map((h) => <th key={h} className="label px-5 py-2.5">{h}</th>)}</tr></thead>
                <tbody className="divide-y divide-ink-800">{[...br.data.strategies].sort((a: any, b: any) => b.rate - a.rate).map((s: any) => (
                  <tr key={s.strategy}><td className="px-5 py-2.5 text-ink-100">{titleCase(s.strategy.split(" ")[0])}{s.strategy.includes("×") ? ` ${s.strategy.split(" ")[1]}` : ""}</td><td className="num px-5">{s.cases}</td><td className="num px-5">{s.recovered}</td>
                    <td className="px-5"><span className="num text-mint-300">{pct(s.rate)}</span></td><td className="num px-5 text-white">{money(s.recovered_minor)}</td></tr>))}</tbody></table>)}
          </Card>
          <Card title="Customer payment behaviour" sub="Customers with the most failed attempts">
            <table className="w-full text-sm"><thead><tr className="border-b border-ink-700 text-left">{["Customer", "Attempts", "Completed", "Failed"].map((h) => <th key={h} className="label px-5 py-2.5">{h}</th>)}</tr></thead>
              <tbody className="divide-y divide-ink-800">{(br.data?.customers ?? []).map((c: any) => (
                <tr key={c.customer}><td className="px-5 py-2.5 text-ink-100">{c.customer}</td><td className="num px-5">{c.attempts}</td><td className="num px-5 text-mint-300">{c.completed}</td><td className="num px-5 text-rose-400">{c.failed}</td></tr>))}</tbody></table>
          </Card>
        </div>
        <p className="mt-4 text-[11px] text-ink-500">Definitions — {Object.entries(o.definitions).map(([k, v]) => `${titleCase(k)}: ${v}`).join(" · ")}</p>
      </>)}
    </div>
  );
}
