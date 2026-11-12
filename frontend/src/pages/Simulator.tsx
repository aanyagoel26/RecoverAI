import { useMutation, useQuery } from "@tanstack/react-query";
import { Calculator, Info } from "lucide-react";
import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ErrorBar, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, ErrorBox, Loading, Notice, PageHeader, Spinner, Toggle } from "../components/ui";
import { api } from "../lib/api";
import { money, pct } from "../lib/format";
import { CHART, recoveredSub, tooltipStyle } from "./Dashboard";

type Sc = { label: string; reminders: number; checkout_assist: boolean };
const DEFAULT: Record<string, Sc> = {
  A: { label: "One recovery reminder", reminders: 1, checkout_assist: false },
  B: { label: "Two personalised reminders", reminders: 2, checkout_assist: false },
  C: { label: "Reminder + checkout assistance", reminders: 2, checkout_assist: true },
};
const ASSUMPTIONS: [string, string][] = [
  ["failed_base_rate", "Failed payment base recovery rate"], ["abandoned_base_rate", "Abandoned checkout base recovery rate"],
  ["pending_resolution_rate", "Pending payments that complete"], ["second_reminder_uplift", "Uplift from 2nd reminder (relative)"],
  ["third_reminder_uplift", "Uplift from 3rd reminder (relative)"], ["checkout_assist_uplift", "Uplift from checkout assistance (relative)"],
];

export function Simulator() {
  const [scen, setScen] = useState(DEFAULT);
  const [assume, setAssume] = useState<Record<string, number> | null>(null);
  const [override, setOverride] = useState("");
  const [selected, setSelected] = useState("C");
  const ov = useQuery({ queryKey: ["overview", 30], queryFn: () => api("/api/analytics/overview?days=30") });
  const sim = useMutation({ mutationFn: () => api("/api/simulator/run", { body: { scenarios: scen, assumptions: assume ?? undefined, at_risk_override_minor: override ? Math.round(Number(override) * 100) : undefined } }) });
  useEffect(() => { sim.mutate(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (sim.data && !assume) setAssume(sim.data.assumptions); }, [sim.data, assume]);

  const r = sim.data;
  const chart = (r?.scenarios ?? []).map((s: any) => ({ key: s.key, name: `${s.key}: ${s.label}`, expected: s.expected_minor / 100, err: [(s.expected_minor - s.low_minor) / 100, (s.high_minor - s.expected_minor) / 100] }));
  const pick = r?.scenarios?.find((s: any) => s.key === selected) ?? r?.scenarios?.[0];
  const funnel = pick ? [{ stage: "Revenue at risk", v: pick.at_risk_minor / 100, c: CHART.amber }, { stage: "Recovery attempts", v: pick.attempted_minor / 100, c: CHART.blue }, { stage: "Est. recovered", v: pick.expected_minor / 100, c: CHART.green }] : [];

  return (
    <div>
      <PageHeader title="Revenue Rescue Simulator" sub="Estimate how much revenue different recovery strategies could win back. Estimates are never counted as recovered revenue."
        actions={<button className="btn-primary" onClick={() => sim.mutate()} disabled={sim.isPending}>{sim.isPending ? <Spinner /> : <Calculator className="h-4 w-4" />} Run simulation</button>} />
      <ErrorBox error={sim.error} />
      {!r ? <Loading label="Simulating" /> : (<>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[["Total revenue at risk", r.pool.total_minor, "text-amberx-400"], ["Failed payment value", r.pool.failed_minor, "text-rose-400"], ["Abandoned checkout value", r.pool.abandoned_minor, "text-amberx-400"], ["Pending payment value", r.pool.pending_minor, "text-volt-300"]].map(([l, v, c]: any) =>
            <div key={l} className="card card-pad"><div className="label">{l}</div><div className={`num mt-1 text-xl font-semibold ${c}`}>{money(v)}</div></div>)}
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <div className="card card-pad"><div className="label">Actual recovered (confirmed, 30d)</div><div className="num mt-1 text-xl font-semibold text-mint-300">{money(ov.data?.revenue_recovered_minor)}</div>{ov.data && <div className="mt-1 text-[11px] text-ink-500">{recoveredSub(ov.data)}</div>}</div>
          <div className="card card-pad"><div className="label">Actual recovery rate</div><div className="num mt-1 text-xl font-semibold text-white">{pct(ov.data?.recovery_conversion_rate)}</div></div>
          <div className="card card-pad"><div className="label">Average recovery time</div><div className="num mt-1 text-xl font-semibold text-white">{ov.data?.avg_hours_to_recovery != null ? `${ov.data.avg_hours_to_recovery}h` : "—"}</div></div>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-3">
          <Card className="xl:col-span-2" title="Strategy comparison" sub="Expected recovered revenue with confidence range">
            <div className="h-72 px-2 py-4">
              <ResponsiveContainer>
                <BarChart data={chart} margin={{ left: 8, right: 16 }}>
                  <CartesianGrid stroke={CHART.grid} vertical={false} />
                  <XAxis dataKey="key" stroke={CHART.axis} fontSize={11} tickLine={false} axisLine={false} tickFormatter={(k) => `Scenario ${k}`} />
                  <YAxis stroke={CHART.axis} fontSize={11} tickLine={false} axisLine={false} tickFormatter={(v) => `$${v >= 1000 ? (v / 1000).toFixed(1) + "k" : v}`} />
                  <Tooltip {...tooltipStyle} cursor={{ fill: CHART.cursor }} formatter={(v: number) => [money(v * 100), "Expected (estimate)"]} labelFormatter={(_, p) => p?.[0]?.payload?.name} />
                  <Bar dataKey="expected" radius={[6, 6, 0, 0]} onClick={(d: any) => setSelected(d.key)} cursor="pointer">
                    {chart.map((c: any) => <Cell key={c.key} fill={c.key === selected ? CHART.green : CHART.blue} fillOpacity={c.key === selected ? 1 : 0.55} />)}
                    <ErrorBar dataKey="err" stroke={CHART.axis} width={8} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="grid gap-px border-t border-ink-800 bg-ink-800 sm:grid-cols-3">
              {r.scenarios.map((s: any) => (
                <button key={s.key} onClick={() => setSelected(s.key)} className={`bg-ink-900 px-5 py-3 text-left ${selected === s.key ? "ring-1 ring-inset ring-mint-500/50" : ""}`}>
                  <div className="text-xs text-ink-400">Scenario {s.key} · {s.label}</div>
                  <div className="num mt-1 text-lg font-semibold text-white">{money(s.expected_minor)}</div>
                  <div className="num text-[11px] text-ink-500">range {money(s.low_minor)} – {money(s.high_minor)}</div>
                </button>))}
            </div>
          </Card>
          <Card title={`Funnel · Scenario ${pick?.key}`} sub="Revenue at risk → recovery attempts → estimated recovered">
            <div className="h-72 px-2 py-4">
              <ResponsiveContainer>
                <BarChart data={funnel} layout="vertical" margin={{ left: 24, right: 16 }}>
                  <XAxis type="number" hide /><YAxis type="category" dataKey="stage" stroke={CHART.axis} fontSize={11} tickLine={false} axisLine={false} width={110} />
                  <Tooltip {...tooltipStyle} cursor={{ fill: CHART.cursor }} formatter={(v: number) => money(v * 100)} />
                  <Bar dataKey="v" radius={[0, 6, 6, 0]}>{funnel.map((f) => <Cell key={f.stage} fill={f.c} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <p className="px-5 pb-4 text-[11px] text-ink-500">Attempts include only customers who consented to recovery messages ({pct(r.contactable_share)} of failed + abandoned value) plus pending payments.</p>
          </Card>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <Card title="Scenarios" sub="Adjust the strategy for each scenario">
            <div className="space-y-3 p-5">
              {Object.entries(scen).map(([k, s]) => (
                <div key={k} className="flex flex-wrap items-center gap-3 rounded-lg border border-ink-700 p-3">
                  <span className="num w-6 font-semibold text-volt-300">{k}</span>
                  <input className="input flex-1 py-1.5" value={s.label} onChange={(e) => setScen({ ...scen, [k]: { ...s, label: e.target.value } })} aria-label={`Scenario ${k} label`} />
                  <label className="flex items-center gap-2 text-xs text-ink-300">Reminders
                    <select className="input w-16 py-1.5" value={s.reminders} onChange={(e) => setScen({ ...scen, [k]: { ...s, reminders: Number(e.target.value) } })}>{[1, 2, 3].map((n) => <option key={n}>{n}</option>)}</select></label>
                  <label className="flex items-center gap-2 text-xs text-ink-300">Checkout assist <Toggle checked={s.checkout_assist} onChange={(v) => setScen({ ...scen, [k]: { ...s, checkout_assist: v } })} label="Checkout assistance" /></label>
                </div>))}
              <label className="block pt-2"><span className="label">What if… revenue at risk (USD, optional)</span>
                <input className="input mt-1" type="number" min="1" placeholder="e.g. 10000 — scales current mix to this amount" value={override} onChange={(e) => setOverride(e.target.value)} /></label>
            </div>
          </Card>
          <Card title="Assumptions" sub={r.basis}>
            <div className="space-y-4 p-5">
              {assume && ASSUMPTIONS.map(([k, l]) => (
                <label key={k} className="block">
                  <span className="flex justify-between text-xs text-ink-300"><span>{l}</span><span className="num text-white">{pct(assume[k])}</span></span>
                  <input type="range" min={0} max={k.includes("uplift") ? 0.6 : 0.95} step={0.01} value={assume[k]} onChange={(e) => setAssume({ ...assume, [k]: Number(e.target.value) })} className="mt-1 w-full accent-[#3B7BFF]" />
                </label>))}
              <Notice tone="amber"><Info className="mr-1 inline h-3.5 w-3.5" />{r.disclaimer} Confidence range widens when this merchant has little closed-case history.</Notice>
            </div>
          </Card>
        </div>
      </>)}
    </div>
  );
}
